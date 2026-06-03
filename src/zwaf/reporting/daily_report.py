"""Sofia Daily Report metrics, formatting and WhatsApp delivery."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger("zwaf.reporting.daily_report")

UNAVAILABLE = "indisponivel"


async def get_daily_metrics(conn: "asyncpg.Connection", tenant_id: str) -> dict:
    """
    Retorna:
    - conversations_today: int
    - sales_today: int
    - conversion_rate: float
    - revenue_today_cents: int
    - total_sales_all_time: int
    """
    conversations = await conn.fetchval(
        "SELECT COUNT(*) FROM zwaf_sessions WHERE tenant_id = $1 AND created_at::date = CURRENT_DATE",
        tenant_id,
    )
    row = await conn.fetchrow(
        "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM payment_events "
        "WHERE tenant_id = $1 AND status = 'PAID' AND created_at::date = CURRENT_DATE",
        tenant_id,
    )
    total = await conn.fetchval(
        "SELECT COUNT(*) FROM payment_events WHERE tenant_id = $1 AND status = 'PAID'",
        tenant_id,
    )
    conversations_count = int(conversations or 0)
    sales_count = int(row[0] or 0) if row else 0
    return {
        "conversations_today": conversations_count,
        "sales_today": sales_count,
        "conversion_rate": (sales_count / conversations_count) if conversations_count else 0.0,
        "revenue_today_cents": int(row[1] or 0) if row else 0,
        "total_sales_all_time": int(total or 0),
    }


def _currency_brl(cents: Any) -> str:
    if cents is None:
        return UNAVAILABLE
    revenue_brl = int(cents) / 100
    return f"R$ {revenue_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _count(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    return str(int(value))


def _percent(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{float(value) * 100:.1f}%".replace(".", ",")


def _stock(metrics: dict, initial_stock: int) -> str:
    total_sales = metrics.get("total_sales_all_time")
    if total_sales is None:
        return UNAVAILABLE
    return f"{initial_stock - int(total_sales)} potes"


def format_report(metrics: dict, date: str, initial_stock: int = 600) -> str:
    """Formata mensagem final em portugues com emojis. Sem dependencia de DB."""
    revenue_str = _currency_brl(metrics.get("revenue_today_cents"))
    return (
        f"*Raiz Vital - Relatorio {date}*\n\n"
        f"Conversas hoje: {_count(metrics.get('conversations_today'))}\n"
        f"Taxa de conversao: {_percent(metrics.get('conversion_rate'))}\n"
        f"Vendas confirmadas: {_count(metrics.get('sales_today'))}\n"
        f"Receita do dia: {revenue_str}\n"
        f"Estoque restante: {_stock(metrics, initial_stock)}\n\n"
        f"Alertas: nenhum\n\n"
        f"_Proximo relatorio: amanha as 20:30_"
    )


def _unavailable_metrics() -> dict:
    return {
        "conversations_today": None,
        "conversion_rate": None,
        "sales_today": None,
        "revenue_today_cents": None,
        "total_sales_all_time": None,
    }


def _clean_asyncpg_url(db_url: str) -> str:
    return db_url.replace("+asyncpg", "")


def _initial_stock_from_env() -> int:
    raw = os.getenv("INITIAL_STOCK_NEW_WOMAN") or os.getenv("INITIAL_STOCK", "600")
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid INITIAL_STOCK value, using default")
        return 600


def _today_brt() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")


async def build_and_send_report(
    db_url: str | None,
    tenant_id: str,
    whatsapp_tool,
    group_id: str,
) -> None:
    """Orquestra: conecta -> metricas -> formata -> envia. Graceful se WA nao configurado."""
    metrics = _unavailable_metrics()

    if not db_url:
        logger.warning("daily_report_db_unavailable: DATABASE_URL not configured")
    else:
        try:
            import asyncpg

            conn = await asyncpg.connect(_clean_asyncpg_url(db_url))
            try:
                metrics = await get_daily_metrics(conn, tenant_id)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("daily_report_db_unavailable: %s", exc)

    message = format_report(metrics, date=_today_brt(), initial_stock=_initial_stock_from_env())

    if not group_id:
        logger.warning("daily_report_whatsapp_group_not_configured")
        return
    if whatsapp_tool is None or not hasattr(whatsapp_tool, "send_message"):
        logger.warning("daily_report_whatsapp_tool_not_configured")
        return

    try:
        await whatsapp_tool.send_message(
            phone=group_id,
            text=message,
            session_id=f"daily_report_{tenant_id}",
        )
    except Exception as exc:
        logger.warning("daily_report_whatsapp_send_failed: %s", exc)
