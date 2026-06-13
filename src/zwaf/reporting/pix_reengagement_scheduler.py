"""PIX re-engagement scheduler — story-051.

Runs hourly. Finds PIX orders expiring within the next 24 hours and sends
a single WhatsApp reminder to the lead. Respects opt-out; stamps
reengagement_sent_at after each successful send.
"""
from __future__ import annotations

import logging

from zwaf.conversion.pix_reengagement import run_pix_reengagement_job

logger = logging.getLogger("zwaf.reporting.pix_reengagement_scheduler")


def register_pix_reengagement_scheduler(
    agno_app,
    db_url: str,
    tenant_id: str,
    whatsapp_tool,
) -> None:
    """Register an hourly APScheduler job for PIX re-engagement.

    Skips registration when db_url is empty (graceful degradation).
    """
    if not db_url:
        logger.warning(
            "pix_reengagement_scheduler not registered — db_url empty tenant=%s",
            tenant_id,
        )
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_pix_reengagement_job,
        trigger="cron",
        minute=0,
        id=f"pix_reengagement_{tenant_id}",
        replace_existing=True,
        kwargs={
            "db_url": db_url,
            "tenant_id": tenant_id,
            "whatsapp_tool": whatsapp_tool,
            "lookahead_days": 1,
        },
    )
    scheduler.start()

    if agno_app is not None and hasattr(agno_app, "state"):
        schedulers = getattr(agno_app.state, "pix_reengagement_schedulers", [])
        schedulers.append(scheduler)
        agno_app.state.pix_reengagement_schedulers = schedulers

    logger.info(
        "pix_reengagement_scheduler registered tenant=%s cron=hourly",
        tenant_id,
    )
