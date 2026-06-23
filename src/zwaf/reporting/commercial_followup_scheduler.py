"""Commercial follow-up scheduler - story-065."""
from __future__ import annotations

import logging

from zwaf.conversion.commercial_followup import run_commercial_followup_job

logger = logging.getLogger("zwaf.reporting.commercial_followup_scheduler")


def register_commercial_followup_scheduler(
    agno_app,
    db_url: str,
    tenant_id: str,
    whatsapp_tool,
) -> None:
    """Register an hourly APScheduler job for commercial follow-ups."""
    if not db_url:
        logger.warning(
            "commercial_followup_scheduler not registered - db_url empty tenant=%s",
            tenant_id,
        )
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_commercial_followup_job,
        trigger="cron",
        minute=15,
        id=f"commercial_followup_{tenant_id}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        kwargs={
            "db_url": db_url,
            "tenant_id": tenant_id,
            "whatsapp_tool": whatsapp_tool,
        },
    )
    scheduler.start()

    if agno_app is not None and hasattr(agno_app, "state"):
        schedulers = getattr(agno_app.state, "commercial_followup_schedulers", [])
        schedulers.append(scheduler)
        agno_app.state.commercial_followup_schedulers = schedulers

    logger.info("commercial_followup_scheduler registered tenant=%s cron=hourly", tenant_id)
