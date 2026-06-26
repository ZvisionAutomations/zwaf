"""Métricas de reativação e recuperação de pedido em aberto (story-044, F6).

Agregados read-only sobre `payment_events` + `leads` para medir se a memória de lead
move o ponteiro: recuperação de pagamentos em aberto (PENDING/EXPIRED → PAID),
clientes recorrentes e o backlog de oportunidades em aberto. Sem UI; chamável de um
CLI/cron. Métrica-norte da story (reativação + recuperação).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from zwaf.db.dsn import normalize_dsn

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger("zwaf.reporting.reactivation")


async def get_reactivation_metrics(conn: "asyncpg.Connection", tenant_id: str) -> dict:
    """Retorna oportunidades em aberto, recuperados, taxa, recorrentes e cobertura."""
    open_opportunities = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT p.lead_phone)
        FROM payment_events p
        WHERE p.tenant_id = $1 AND p.status IN ('PENDING', 'EXPIRED')
          AND NOT EXISTS (
              SELECT 1 FROM payment_events q
              WHERE q.tenant_id = p.tenant_id AND q.lead_phone = p.lead_phone
                AND q.status = 'PAID'
          )
        """,
        tenant_id,
    )
    recovered = await conn.fetchval(
        """
        SELECT COUNT(DISTINCT p.lead_phone)
        FROM payment_events p
        WHERE p.tenant_id = $1 AND p.status IN ('PENDING', 'EXPIRED')
          AND EXISTS (
              SELECT 1 FROM payment_events q
              WHERE q.tenant_id = p.tenant_id AND q.lead_phone = p.lead_phone
                AND q.status = 'PAID'
          )
        """,
        tenant_id,
    )
    repeat_buyers = await conn.fetchval(
        """
        SELECT COUNT(*) FROM (
            SELECT lead_phone FROM payment_events
            WHERE tenant_id = $1 AND status = 'PAID'
            GROUP BY lead_phone HAVING COUNT(*) >= 2
        ) t
        """,
        tenant_id,
    )
    leads_with_memory = await conn.fetchval(
        "SELECT COUNT(*) FROM leads WHERE tenant_id = $1 AND memory_updated_at IS NOT NULL",
        tenant_id,
    )

    open_n = int(open_opportunities or 0)
    rec_n = int(recovered or 0)
    denom = open_n + rec_n
    return {
        "open_payment_opportunities": open_n,
        "recovered_payments": rec_n,
        "recovery_rate": (rec_n / denom) if denom else 0.0,
        "repeat_buyers": int(repeat_buyers or 0),
        "leads_with_memory": int(leads_with_memory or 0),
    }


def format_reactivation_report(metrics: dict) -> str:
    """Formata o relatório em português. Sem dependência de DB."""
    rate = f"{metrics.get('recovery_rate', 0.0) * 100:.1f}%".replace(".", ",")
    return (
        "*Reativacao / Recuperacao*\n"
        f"Pedidos em aberto (oportunidade): {metrics.get('open_payment_opportunities', 0)}\n"
        f"Recuperados (em aberto -> pago): {metrics.get('recovered_payments', 0)}\n"
        f"Taxa de recuperacao: {rate}\n"
        f"Clientes recorrentes (2+ compras): {metrics.get('repeat_buyers', 0)}\n"
        f"Leads com memoria: {metrics.get('leads_with_memory', 0)}"
    )


def _clean_asyncpg_url(db_url: str) -> str:
    return normalize_dsn(db_url)


async def build_reactivation_report(db_url: str | None, tenant_id: str) -> dict:
    """Conecta, computa as métricas e devolve o dict. Gracioso quando não há DB."""
    if not db_url:
        logger.warning("reactivation_report_db_unavailable")
        return {}
    try:
        import asyncpg

        conn = await asyncpg.connect(_clean_asyncpg_url(db_url))
        try:
            return await get_reactivation_metrics(conn, tenant_id)
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("reactivation_report failed: %s", exc)
        return {}
