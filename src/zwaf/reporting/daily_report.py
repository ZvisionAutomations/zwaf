"""Raiz Vital Daily Report metrics, formatting and WhatsApp delivery."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from zwaf.db.dsn import normalize_dsn

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger("zwaf.reporting.daily_report")

UNAVAILABLE = "indisponivel"

# Story-076: idempotencia de envio por (grupo + dia). Duas fontes de duplicacao em prod:
# (1) o scheduler e registrado POR TENANT (api/main.py lifespan); (2) o container roda com
# uvicorn --workers 2, entao CADA worker executa o lifespan e registra seu proprio scheduler.
# A guarda in-process abaixo so cobre o mesmo processo (multi-tenant); para cobrir os WORKERS
# (processos distintos) e restarts, usamos um claim ATOMICO no banco (daily_report_sent,
# migration 011). Com db_url, o banco e a fonte de verdade; sem db_url (testes/degradado),
# cai na guarda in-process.
_sent_reports_by_group: dict[str, str] = {}


def reset_daily_report_dedupe() -> None:
    """Limpa o registro in-process de idempotencia do relatorio diario (uso em testes)."""
    _sent_reports_by_group.clear()


async def _claim_daily_report_slot(db_url: str, group_id: str, report_date: str) -> bool:
    """Reserva atomica do envio (grupo+dia) no banco. True se ESTE processo ganhou o slot
    (deve enviar); False se outro worker ja reservou. Cobre multi-worker e restart.

    Degrada para True se o banco estiver indisponivel: melhor arriscar 1-2 envios do que
    silenciar o relatorio por falha de infra.
    """
    try:
        import asyncpg

        conn = await asyncpg.connect(_clean_asyncpg_url(db_url))
        try:
            won = await conn.fetchval(
                "INSERT INTO daily_report_sent (group_id, report_date) VALUES ($1, $2) "
                "ON CONFLICT (group_id, report_date) DO NOTHING RETURNING 1",
                group_id,
                report_date,
            )
            return won is not None
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("daily_report_claim_db_unavailable: %s", exc)
        return True


async def _release_daily_report_slot(db_url: str, group_id: str, report_date: str) -> None:
    """Libera o slot reservado (usado quando o envio falha, para permitir retry)."""
    try:
        import asyncpg

        conn = await asyncpg.connect(_clean_asyncpg_url(db_url))
        try:
            await conn.execute(
                "DELETE FROM daily_report_sent WHERE group_id = $1 AND report_date = $2",
                group_id,
                report_date,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("daily_report_release_db_unavailable: %s", exc)


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


def _metric_number(metrics: dict, key: str) -> float:
    value = metrics.get(key)
    if value is None:
        return 0
    return float(value)


def _build_alerts(metrics: dict, initial_stock: int) -> list[str]:
    total_sales_all_time = _metric_number(metrics, "total_sales_all_time")
    sales_today = _metric_number(metrics, "sales_today")
    conversations_today = _metric_number(metrics, "conversations_today")
    conversion_rate = _metric_number(metrics, "conversion_rate")

    alerts = []
    if (initial_stock - total_sales_all_time) < 50:
        alerts.append("⚠️  Estoque crítico: menos de 50 potes restantes")
    if sales_today == 0 and conversations_today >= 5:
        alerts.append(f"⚠️  Nenhuma venda hoje com {int(conversations_today)} conversas ativas")
    if conversations_today >= 10 and conversion_rate < 0.05:
        alerts.append(f"⚠️  Taxa de conversão abaixo de 5% ({conversion_rate:.1%})")
    if conversations_today == 0:
        alerts.append("⚠️  Nenhuma conversa iniciada hoje")
    return alerts


def format_report(metrics: dict, date: str, initial_stock: int = 600) -> str:
    """Formata mensagem final em portugues com emojis. Sem dependencia de DB."""
    revenue_str = _currency_brl(metrics.get("revenue_today_cents"))
    alerts = _build_alerts(metrics, initial_stock)
    alerts_str = "\n".join(f"• {alert}" for alert in alerts) if alerts else "nenhum"
    return (
        f"*Raiz Vital - Relatorio {date}*\n\n"
        f"Conversas hoje: {_count(metrics.get('conversations_today'))}\n"
        f"Taxa de conversao: {_percent(metrics.get('conversion_rate'))}\n"
        f"Vendas confirmadas: {_count(metrics.get('sales_today'))}\n"
        f"Receita do dia: {revenue_str}\n"
        f"Estoque restante: {_stock(metrics, initial_stock)}\n\n"
        f"Alertas: {alerts_str}\n\n"
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
    return normalize_dsn(db_url)


def _initial_stock_from_env() -> int:
    raw = os.getenv("INITIAL_STOCK_NEW_WOMAN") or os.getenv("INITIAL_STOCK") or "600"
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

    # Story-076: 1 relatorio por grupo/dia. Com db_url, o claim atomico no banco cobre os
    # 2 workers uvicorn e restarts (fonte de verdade). Sem db_url (testes/degradado), usa a
    # guarda in-process, reservando ANTES do await para nao duplicar com tenants concorrentes
    # no mesmo event loop.
    today = _today_brt()
    if db_url:
        if not await _claim_daily_report_slot(db_url, group_id, today):
            logger.info(
                "daily_report_skipped_duplicate_db group=%s date=%s tenant=%s",
                group_id, today, tenant_id,
            )
            return
    else:
        if _sent_reports_by_group.get(group_id) == today:
            logger.info(
                "daily_report_skipped_duplicate group=%s date=%s tenant=%s",
                group_id, today, tenant_id,
            )
            return
        _sent_reports_by_group[group_id] = today

    try:
        await whatsapp_tool.send_message(
            phone=group_id,
            text=message,
            session_id=f"daily_report_{tenant_id}",
        )
    except Exception as exc:
        # Falhou: libera o slot (banco ou in-process) para permitir nova tentativa no dia.
        if db_url:
            await _release_daily_report_slot(db_url, group_id, today)
        else:
            _sent_reports_by_group.pop(group_id, None)
        logger.warning("daily_report_whatsapp_send_failed: %s", exc)
