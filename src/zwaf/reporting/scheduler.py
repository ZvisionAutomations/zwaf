"""Daily report scheduler integration."""
from __future__ import annotations

import logging
import os

from zwaf.reporting.daily_report import build_and_send_report

logger = logging.getLogger("zwaf.reporting.scheduler")

DAILY_REPORT_CRON_UTC = "30 23 * * *"


def register_daily_report_scheduler(agno_app, db_url: str, tenant_id: str, whatsapp_tool) -> None:
    """
    Registra cron job diario: 20:30 BRT = 23:30 UTC.

    Se REPORT_WA_GROUP_ID ou REPORT_WA_DEST_NUMBER nao definido: loga WARNING e nao registra o job.
    """
    group_id = os.getenv("REPORT_WA_GROUP_ID") or os.getenv("REPORT_WA_DEST_NUMBER", "")
    if not group_id:
        logger.warning("REPORT_WA_GROUP_ID not configured; daily report scheduler not registered")
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        build_and_send_report,
        trigger="cron",
        hour=23,
        minute=30,
        id=f"daily_report_{tenant_id}",
        replace_existing=True,
        kwargs={
            "db_url": db_url,
            "tenant_id": tenant_id,
            "whatsapp_tool": whatsapp_tool,
            "group_id": group_id,
        },
    )
    scheduler.start()

    if agno_app is not None and hasattr(agno_app, "state"):
        schedulers = getattr(agno_app.state, "daily_report_schedulers", [])
        schedulers.append(scheduler)
        agno_app.state.daily_report_schedulers = schedulers

    logger.info(
        "daily_report_scheduler_registered tenant=%s cron_utc=%s",
        tenant_id,
        DAILY_REPORT_CRON_UTC,
    )
