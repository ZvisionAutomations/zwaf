"""Lead store — PostgreSQL (histórico de compra e dados do lead)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from zwaf.security.pii import can_encrypt_pii, decrypt_pii, encrypt_pii

logger = logging.getLogger("zwaf.memory.lead_store")

# Story-044: retenção da memória de lead (LGPD). 24 meses, alinhado ao TTL de
# `record_purchase` no Redis (session.py). ~30 dias/mês para o cutoff.
_MEMORY_RETENTION_MONTHS = 24


def _affected(command_status: str) -> int:
    """Parse the row count from an asyncpg command tag like 'UPDATE 1'."""
    try:
        return int(str(command_status).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0

_DB_URL: Optional[str] = None


def _db_url() -> str:
    return (_DB_URL or os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")


def configure(db_url: str) -> None:
    global _DB_URL
    _DB_URL = db_url


async def get_lead(phone: str, tenant_id: str) -> Optional[dict]:
    """Retorna dados do lead ou None se não encontrado."""
    db_url = _db_url()
    if not db_url:
        return None
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        row = await conn.fetchrow(
            "SELECT * FROM leads WHERE tenant_id = $1 AND phone = $2",
            tenant_id, phone,
        )
        await conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.warning("lead_store.get_lead failed: %s", e)
        return None


async def upsert_lead(
    phone: str,
    tenant_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    last_agent: Optional[str] = None,
) -> None:
    """Insere ou atualiza lead no PostgreSQL."""
    db_url = _db_url()
    if not db_url:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        await conn.execute(
            """
            INSERT INTO leads (tenant_id, phone, name, email, last_agent, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE
              SET name = COALESCE(EXCLUDED.name, leads.name),
                  email = COALESCE(EXCLUDED.email, leads.email),
                  last_agent = COALESCE(EXCLUDED.last_agent, leads.last_agent),
                  updated_at = NOW()
            """,
            tenant_id, phone, name, email, last_agent,
        )
        await conn.close()
    except Exception as e:
        logger.warning("lead_store.upsert_lead failed: %s", e)


async def mark_opt_out(
    phone: str,
    tenant_id: str,
    reason: str = "lead_requested",
) -> None:
    """Marca lead como opt-out em tabelas novas e legadas, best-effort."""
    db_url = _db_url()
    if not db_url:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                INSERT INTO lead_profiles (
                    tenant_id, phone, opt_out_at, opt_out_reason, contact_status, updated_at
                )
                VALUES ($1, $2, NOW(), $3, 'opted_out', NOW())
                ON CONFLICT (tenant_id, phone) DO UPDATE SET
                    opt_out_at = COALESCE(lead_profiles.opt_out_at, NOW()),
                    opt_out_reason = EXCLUDED.opt_out_reason,
                    contact_status = 'opted_out',
                    updated_at = NOW()
                """,
                tenant_id, phone, reason,
            )
            await conn.execute(
                """
                UPDATE leads
                SET opt_out_at = COALESCE(opt_out_at, NOW()),
                    opt_out_reason = $3,
                    contact_status = 'opted_out',
                    primary_symptom_enc = NULL,
                    memory_summary_enc = NULL,
                    objections = '[]'::jsonb,
                    next_best_action = NULL,
                    memory_purged_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = $1 AND phone = $2
                """,
                tenant_id, phone, reason,
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("lead_store.mark_opt_out failed: %s", e)


async def append_purchase_history(
    phone: str,
    tenant_id: str,
    product_id: str,
    amount_cents: int,
) -> None:
    """Adiciona entrada ao histórico de compra do lead."""
    db_url = _db_url()
    if not db_url:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        entry = json.dumps({"product_id": product_id, "amount_cents": amount_cents})
        await conn.execute(
            """
            UPDATE leads
            SET purchase_history = purchase_history || $3::jsonb,
                updated_at = NOW()
            WHERE tenant_id = $1 AND phone = $2
            """,
            tenant_id, phone, f"[{entry}]",
        )
        await conn.close()
    except Exception as e:
        logger.warning("lead_store.append_purchase_history failed: %s", e)


async def append_conversion_event(
    phone: str,
    tenant_id: str,
    session_id: str,
    lead_id: str,
    agent_name: str,
    signal: dict[str, Any],
) -> None:
    """Persist conversion signal best-effort for funnel analysis."""
    db_url = _db_url()
    if not db_url:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        await conn.execute(
            """
            INSERT INTO conversion_events (
                tenant_id, lead_phone, session_id, lead_id, agent_name,
                sentiment, buying_intent, action, should_send_payment_link,
                confidence, reasons, raw_signal
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb)
            """,
            tenant_id,
            phone,
            session_id,
            lead_id,
            agent_name,
            signal.get("sentiment"),
            signal.get("buying_intent"),
            signal.get("action"),
            signal.get("should_send_payment_link", False),
            float(signal.get("confidence", 0.0)),
            json.dumps(signal.get("reasons", [])),
            json.dumps(signal),
        )
        await conn.close()
    except Exception as e:
        logger.warning("lead_store.append_conversion_event failed: %s", e)


# ─── Memória de lead (story-044) ──────────────────────────────────────


async def upsert_lead_memory(
    phone: str,
    tenant_id: str,
    *,
    primary_symptom: Optional[str] = None,
    objections: Optional[list] = None,
    memory_summary: Optional[str] = None,
    next_best_action: Optional[str] = None,
) -> None:
    """Grava/atualiza a memória semântica do lead (story-044).

    `primary_symptom` e `memory_summary` são dado de saúde (LGPD Art. 11): cifrados
    com Fernet antes de persistir. Sem chave Fernet disponível, esses campos NÃO
    são gravados (degradação graciosa — nunca em claro). `objections` e
    `next_best_action` são comerciais e ficam em claro. Campos não informados
    (None) preservam o valor existente (COALESCE).
    """
    db_url = _db_url()
    if not db_url:
        return

    symptom_enc = (
        encrypt_pii(primary_symptom) if (primary_symptom and can_encrypt_pii()) else None
    )
    summary_enc = (
        encrypt_pii(memory_summary) if (memory_summary and can_encrypt_pii()) else None
    )
    if (primary_symptom or memory_summary) and not can_encrypt_pii():
        logger.warning(
            "lead_store.upsert_lead_memory: PII key unavailable — health fields not stored"
        )
    objections_json = json.dumps(objections) if objections is not None else None

    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                INSERT INTO leads (
                    tenant_id, phone,
                    primary_symptom_enc, memory_summary_enc, objections,
                    next_best_action, memory_updated_at, updated_at
                )
                VALUES ($1, $2, $3, $4, COALESCE($5::jsonb, '[]'::jsonb), $6, NOW(), NOW())
                ON CONFLICT (tenant_id, phone) DO UPDATE SET
                    primary_symptom_enc = COALESCE(EXCLUDED.primary_symptom_enc, leads.primary_symptom_enc),
                    memory_summary_enc  = COALESCE(EXCLUDED.memory_summary_enc, leads.memory_summary_enc),
                    objections          = COALESCE($5::jsonb, leads.objections),
                    next_best_action    = COALESCE(EXCLUDED.next_best_action, leads.next_best_action),
                    memory_updated_at   = NOW(),
                    updated_at          = NOW()
                """,
                tenant_id, phone, symptom_enc, summary_enc, objections_json, next_best_action,
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("lead_store.upsert_lead_memory failed: %s", e)


async def get_lead_memory(phone: str, tenant_id: str) -> Optional[dict]:
    """Lê e decifra a memória semântica do lead (story-044) ou None.

    Os campos de saúde são decifrados apenas em memória de processo (para montar o
    bloco reinjetado), nunca re-persistidos nem logados.
    """
    db_url = _db_url()
    if not db_url:
        return None
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                """
                SELECT name, primary_symptom_enc, memory_summary_enc, objections,
                       next_best_action, memory_updated_at, memory_purged_at
                FROM leads WHERE tenant_id = $1 AND phone = $2
                """,
                tenant_id, phone,
            )
        finally:
            await conn.close()
        if not row:
            return None

        objections = row["objections"]
        if isinstance(objections, str):
            try:
                objections = json.loads(objections)
            except (ValueError, TypeError):
                objections = []

        return {
            "name": row["name"],
            "primary_symptom": decrypt_pii(row["primary_symptom_enc"]) if row["primary_symptom_enc"] else "",
            "memory_summary": decrypt_pii(row["memory_summary_enc"]) if row["memory_summary_enc"] else "",
            "objections": objections or [],
            "next_best_action": row["next_best_action"] or "",
            "memory_updated_at": row["memory_updated_at"],
            "memory_purged_at": row["memory_purged_at"],
        }
    except Exception as e:
        logger.warning("lead_store.get_lead_memory failed: %s", e)
        return None


async def purge_expired_memory(
    tenant_id: str,
    retention_months: int = _MEMORY_RETENTION_MONTHS,
    now: Optional[datetime] = None,
) -> int:
    """Purga memória de leads não atualizada há mais de `retention_months` (LGPD).

    Idempotente: só toca linhas que ainda têm memória (não re-purga linhas já
    limpas). Retorna o número de leads purgados. Log sem PII.
    """
    db_url = _db_url()
    if not db_url:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=30 * retention_months)
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            status = await conn.execute(
                """
                UPDATE leads
                SET primary_symptom_enc = NULL,
                    memory_summary_enc = NULL,
                    objections = '[]'::jsonb,
                    next_best_action = NULL,
                    memory_purged_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND memory_updated_at IS NOT NULL
                  AND memory_updated_at < $2
                  AND (
                       primary_symptom_enc IS NOT NULL
                    OR memory_summary_enc IS NOT NULL
                    OR next_best_action IS NOT NULL
                    OR objections <> '[]'::jsonb
                  )
                """,
                tenant_id, cutoff,
            )
        finally:
            await conn.close()
        return _affected(status)
    except Exception as e:
        logger.warning("lead_store.purge_expired_memory failed: %s", e)
        return 0
