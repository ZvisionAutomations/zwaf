"""Admin route para metricas de A/B testing (story-056)."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter

from zwaf.db.dsn import normalize_dsn

router = APIRouter()
logger = logging.getLogger("zwaf.api.ab_test")

_DB_URL_RAW = os.getenv("DATABASE_URL", "")
_DB_URL = normalize_dsn(_DB_URL_RAW)


@router.get("/ab-test/{test_name}")
async def get_ab_test_metrics(test_name: str, tenant_id: str = "") -> dict:
    """Retorna metricas de conversao por variante A/B."""
    if not _DB_URL:
        return {"test_name": test_name, "tenant_id": tenant_id, "variants": {}}
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
        try:
            rows = await conn.fetch(
                """
                SELECT
                    a.variant,
                    COUNT(DISTINCT a.phone) AS assigned,
                    COUNT(DISTINCT pe.id) AS conversions
                FROM ab_assignments a
                LEFT JOIN payment_events pe
                    ON pe.lead_phone = a.phone
                   AND pe.tenant_id = a.tenant_id
                   AND pe.status = 'PAID'
                WHERE a.tenant_id = $1 AND a.test_name = $2
                GROUP BY a.variant
                ORDER BY a.variant
                """,
                tenant_id,
                test_name,
            )
        finally:
            await conn.close()

        variants = {}
        for row in rows:
            assigned = int(row["assigned"] or 0)
            conversions = int(row["conversions"] or 0)
            variants[row["variant"]] = {
                "assigned": assigned,
                "conversions": conversions,
                "conversion_rate": round(conversions / assigned, 4) if assigned else 0.0,
            }
        return {"test_name": test_name, "tenant_id": tenant_id, "variants": variants}
    except Exception:
        logger.exception("Failed to fetch AB test metrics")
        return {"test_name": test_name, "tenant_id": tenant_id, "variants": {}}
