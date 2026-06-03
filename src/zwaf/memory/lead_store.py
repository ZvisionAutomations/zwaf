"""Lead store — PostgreSQL (histórico de compra e dados do lead)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("zwaf.memory.lead_store")

_DB_URL: Optional[str] = None


def _db_url() -> str:
    return (_DB_URL or os.getenv("DATABASE_URL", "")).replace("+asyncpg", "")


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
