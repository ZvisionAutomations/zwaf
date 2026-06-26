"""Commercial follow-up engine -- story-065.

Runs hourly. For each tenant, finds leads with pending follow-up
(status='pending', next_send_at <= NOW()), calls build_followup_plan
to get the next contact text, sends via WhatsApp, and updates state.
Respects opt-out, medical_risk, idempotency, and hourly windows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from zwaf.conversion.followup import FollowupStage, build_followup_plan
from zwaf.conversion.pix_reengagement import is_opted_out

logger = logging.getLogger("zwaf.conversion.commercial_followup")

# Backoff applied when a send fails so the row is retried on the next hourly
# run instead of being orphaned with a NULL next_send_at (story-065 MEDIUM-2).
_SEND_RETRY_BACKOFF_HOURS = 1


async def enroll_lead_for_followup(
    db_url: str,
    tenant_id: str,
    lead_phone: str,
    stage: str,
    temperature: str = "warm",
) -> bool:
    """Enroll a lead in the commercial follow-up pipeline.

    Returns True if a new row was inserted, False if it already existed.
    Idempotent: ON CONFLICT DO NOTHING.
    """
    if not db_url or not lead_phone:
        return False
    try:
        conn = await asyncpg.connect(db_url)
        try:
            result = await conn.execute(
                """
                INSERT INTO commercial_followups
                    (tenant_id, lead_phone, stage, temperature, next_send_at)
                VALUES ($1, $2, $3, $4, NOW() + INTERVAL '1 hour')
                ON CONFLICT (tenant_id, lead_phone, stage) DO NOTHING
                """,
                tenant_id,
                lead_phone,
                stage,
                temperature,
            )
        finally:
            await conn.close()
        # asyncpg returns "INSERT 0 N" -- N=1 means inserted, N=0 means conflict
        return result.endswith("1")
    except Exception as exc:
        logger.error(
            "enroll_lead_for_followup failed tenant=%s phone=%s: %s",
            tenant_id,
            (lead_phone[:4] + "****") if lead_phone else "",
            exc,
        )
        return False


async def get_due_followups(db_url: str, tenant_id: str) -> list[dict]:
    """Return followups that are due to be sent right now."""
    if not db_url:
        return []
    try:
        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT id, lead_phone, stage, temperature, contacts_sent
                FROM commercial_followups
                WHERE tenant_id = $1
                  AND status = 'pending'
                  AND next_send_at <= NOW()
                ORDER BY next_send_at ASC
                """,
                tenant_id,
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_due_followups failed tenant=%s: %s", tenant_id, exc)
        return []


async def update_followup_state(
    db_url: str,
    followup_id: str,
    *,
    status: str,
    contacts_sent: int,
    next_send_at: Optional[datetime] = None,
) -> None:
    """Update the state of a follow-up row."""
    if not db_url or not followup_id:
        return
    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                UPDATE commercial_followups
                SET status = $2,
                    contacts_sent = $3,
                    next_send_at = $4,
                    updated_at = NOW()
                WHERE id = $1
                """,
                followup_id,
                status,
                contacts_sent,
                next_send_at,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("update_followup_state failed id=%s: %s", followup_id, exc)


async def mark_followup_replied(db_url: str, tenant_id: str, lead_phone: str) -> None:
    """Mark all pending follow-ups for a lead as replied (they responded)."""
    if not db_url or not lead_phone:
        return
    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                UPDATE commercial_followups
                SET status = 'replied',
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND lead_phone = $2
                  AND status = 'pending'
                """,
                tenant_id,
                lead_phone,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error(
            "mark_followup_replied failed tenant=%s: %s", tenant_id, exc
        )


async def run_commercial_followup_job(
    db_url: str,
    tenant_id: str,
    whatsapp_tool: Any,
) -> int:
    """Run one cycle of the commercial follow-up job.

    Returns the number of messages successfully sent.
    """
    if not db_url or not whatsapp_tool:
        return 0

    due = await get_due_followups(db_url, tenant_id)
    if not due:
        return 0

    sent = 0
    for row in due:
        followup_id: str = str(row.get("id") or "")
        lead_phone: str = str(row.get("lead_phone") or "")
        stage: str = str(row.get("stage") or "")
        temperature: str = str(row.get("temperature") or "warm")
        contacts_sent: int = int(row.get("contacts_sent") or 0)

        if not followup_id or not lead_phone or not stage:
            continue

        # Optimistic lock: try to claim 'sending' before doing any I/O
        try:
            conn = await asyncpg.connect(db_url)
            try:
                claimed_id = await conn.fetchval(
                    """
                    UPDATE commercial_followups
                    SET status = 'sending', updated_at = NOW()
                    WHERE id = $1 AND status = 'pending'
                    RETURNING id
                    """,
                    followup_id,
                )
            finally:
                await conn.close()

            if not claimed_id:
                # Another process already picked this row up
                continue
        except Exception as exc:
            logger.error(
                "commercial_followup lock failed id=%s: %s", followup_id, exc
            )
            continue

        # Check opt-out
        opted_out = await is_opted_out(db_url, tenant_id, lead_phone)
        if opted_out:
            await update_followup_state(
                db_url,
                followup_id,
                status="opted_out",
                contacts_sent=contacts_sent,
            )
            continue

        # Build the plan from the temperature persisted at enrollment — the plan
        # is the single source of truth for cadence/limits (story-065 HIGH-4).
        # No synthetic message text is fabricated to coax a classification.
        try:
            stage_enum = FollowupStage(stage)
        except ValueError:
            logger.error(
                "commercial_followup unknown stage=%s id=%s", stage, followup_id
            )
            await update_followup_state(
                db_url,
                followup_id,
                status="error",
                contacts_sent=contacts_sent,
            )
            continue

        plan = build_followup_plan(
            messages=[],
            stage=stage_enum,
            contacts_already_sent=contacts_sent,
            temperature_override=temperature,
        )

        if not plan.allowed:
            # Normalize the plan's opt-out reason to the engine's terminal status.
            terminal = "opted_out" if plan.reason == "opt_out" else plan.reason
            await update_followup_state(
                db_url,
                followup_id,
                status=terminal,
                contacts_sent=contacts_sent,
            )
            continue

        if not plan.contacts:
            await update_followup_state(
                db_url,
                followup_id,
                status="limit_reached",
                contacts_sent=contacts_sent,
            )
            continue

        next_contact = plan.contacts[0]
        try:
            await whatsapp_tool(phone=lead_phone, message=next_contact.text)

            new_sent = contacts_sent + 1
            # Ask the plan for the FOLLOWING contact to learn its delay and whether
            # more are allowed — keeps the plan as the single source of truth and
            # respects per-temperature limits (story-065 HIGH-3).
            next_plan = build_followup_plan(
                messages=[],
                stage=stage_enum,
                contacts_already_sent=new_sent,
                temperature_override=temperature,
            )
            if next_plan.allowed and next_plan.contacts:
                new_status = "pending"
                next_send = datetime.now(timezone.utc) + timedelta(
                    hours=next_plan.contacts[0].delay_hours
                )
            else:
                new_status = "limit_reached"
                next_send = None

            await update_followup_state(
                db_url,
                followup_id,
                status=new_status,
                contacts_sent=new_sent,
                next_send_at=next_send,
            )
            sent += 1
            logger.info(
                "commercial_followup sent id=%s phone=%s stage=%s contacts_sent=%d",
                followup_id,
                lead_phone[:4] + "****",
                stage,
                new_sent,
            )
        except Exception as exc:
            logger.error(
                "commercial_followup send failed phone=%s: %s",
                lead_phone[:4] + "****",
                exc,
            )
            # Revert to pending WITH a backoff so the row is retried on the next
            # hourly run instead of being orphaned with a NULL next_send_at, which
            # the due-query (next_send_at <= NOW()) would never select (MEDIUM-2).
            await update_followup_state(
                db_url,
                followup_id,
                status="pending",
                contacts_sent=contacts_sent,
                next_send_at=datetime.now(timezone.utc)
                + timedelta(hours=_SEND_RETRY_BACKOFF_HOURS),
            )

    return sent