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

from zwaf.conversion.followup import (
    FollowupStage,
    build_followup_plan,
    HOT_DELAY_HOURS,
)
from zwaf.conversion.pix_reengagement import is_opted_out

logger = logging.getLogger("zwaf.conversion.commercial_followup")


def _synthetic_messages_for_temperature(temperature: str) -> list[str]:
    """Return synthetic messages that produce the desired lead temperature classification."""
    if temperature == "hot":
        return ["quero comprar", "qual o preco", "manda o link"]
    if temperature == "warm":
        return ["quero saber mais"]
    if temperature == "cold":
        return ["ola"]
    # RISK or unknown -- return empty; build_followup_plan will handle
    return []


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
        import asyncpg

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
        import asyncpg

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
        import asyncpg

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
        import asyncpg

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
            import asyncpg

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

        # Build followup plan using synthetic messages matching the lead temperature
        messages = _synthetic_messages_for_temperature(temperature)
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
            messages=messages,
            stage=stage_enum,
            contacts_already_sent=contacts_sent,
        )

        if not plan.allowed:
            await update_followup_state(
                db_url,
                followup_id,
                status=plan.reason,
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
            next_delay = HOT_DELAY_HOURS[new_sent] if new_sent < len(HOT_DELAY_HOURS) else None
            new_status = (
                "pending"
                if (new_sent < plan.max_contacts and next_delay is not None)
                else "limit_reached"
            )
            next_send = (
                datetime.now(timezone.utc) + timedelta(hours=next_delay)
                if (next_delay is not None and new_status == "pending")
                else None
            )

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
            # Revert to pending so it retries next hour
            await update_followup_state(
                db_url,
                followup_id,
                status="pending",
                contacts_sent=contacts_sent,
            )

    return sent