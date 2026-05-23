"""Lead store — PostgreSQL (histórico de compra e dados do lead)."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("zwaf.memory.lead_store")

_DB_URL: Optional[str] = None


def configure(db_url: str) -> None:
    global _DB_URL
    _DB_URL = db_url


async def get_lead(phone: str, tenant_id: str) -> Optional[dict]:
    """Retorna dados do lead ou None se não encontrado."""
    if not _DB_URL:
        return None
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
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
    if not _DB_URL:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
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


async def append_purchase_history(
    phone: str,
    tenant_id: str,
    product_id: str,
    amount_cents: int,
) -> None:
    """Adiciona entrada ao histórico de compra do lead."""
    if not _DB_URL:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
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
