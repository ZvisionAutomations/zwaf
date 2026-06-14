"""A/B testing de prompt - atribuicao deterministica e persistencia."""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger("zwaf.ab_testing")


def get_variant(
    phone: str,
    tenant_id: str,
    test_name: str = "vendedor_prompt",
    split: float = 0.5,
) -> str:
    """Atribuicao deterministica - mesmo phone sempre recebe mesma variante."""
    key = f"{phone}:{tenant_id}:{test_name}"
    digest = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return "A" if (digest % 100) < int(split * 100) else "B"


async def record_assignment(
    phone: str,
    tenant_id: str,
    test_name: str,
    variant: str,
    db_url: str,
) -> None:
    """Persiste assignment no DB - best-effort, nunca lanca excecao."""
    if not db_url:
        return
    try:
        import asyncpg
        url = db_url.replace("+asyncpg", "")
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(
                """
                INSERT INTO ab_assignments (phone, tenant_id, test_name, variant)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (phone, tenant_id, test_name) DO NOTHING
                """,
                phone,
                tenant_id,
                test_name,
                variant,
            )
        finally:
            await conn.close()
    except Exception:
        logger.debug("Failed to record AB assignment", exc_info=True)
