"""Runtime commercial follow-up engine for Livia.

Consumes the approved deterministic policy from ``followup.py`` and persists
state in ``commercial_followups`` so process restarts do not duplicate sends.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from zwaf.conversion.followup import FollowupContact, FollowupPlan, FollowupStage, build_followup_plan
from zwaf.conversion.funnel_events import FunnelEventName, build_funnel_event
from zwaf.observability import langfuse as _obs

logger = logging.getLogger("zwaf.conversion.commercial_followup")

BRT = ZoneInfo("America/Sao_Paulo")
COMMERCIAL_START_HOUR = 8
COMMERCIAL_END_HOUR = 18
DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class FollowupCandidate:
    phone: str
    stage: FollowupStage
    messages: str
    last_activity_at: datetime
    dry_or_resistant: bool = False


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")


async def run_commercial_followup_job(
    db_url: str,
    tenant_id: str,
    whatsapp_tool: Any,
    *,
    now: Optional[datetime] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = 50,
) -> int:
    """Schedule and send due commercial follow-ups. Returns successful sends."""
    if not db_url or not whatsapp_tool:
        return 0

    now_utc = _as_utc(now or datetime.now(timezone.utc))
    await schedule_commercial_followups(
        db_url,
        tenant_id,
        now=now_utc,
        lookback_days=lookback_days,
    )

    due = await claim_due_followups(db_url, tenant_id, now=now_utc, limit=limit)
    sent = 0
    for row in due:
        phone = str(row.get("phone") or "")
        if not phone:
            await mark_followup_blocked(db_url, str(row.get("id") or ""), "missing_phone")
            continue

        if await is_followup_opted_out(db_url, tenant_id, phone):
            await mark_followup_blocked(db_url, str(row.get("id") or ""), "opt_out")
            continue

        plan = build_followup_plan(
            messages=str(row.get("context_messages") or ""),
            stage=str(row.get("stage") or FollowupStage.POST_OFFER.value),
            contacts_already_sent=int(row.get("contacts_sent") or 0),
        )
        if not plan.allowed or not plan.contacts:
            await mark_followup_blocked(db_url, str(row.get("id") or ""), plan.reason)
            continue

        contact = plan.contacts[0]
        try:
            ok = await _send_whatsapp(
                whatsapp_tool,
                phone=phone,
                text=contact.text,
                session_id=f"followup:{tenant_id}:{phone}",
            )
            if not ok:
                await release_followup_claim(db_url, str(row.get("id") or ""), retry_minutes=30)
                continue
            await mark_followup_sent(
                db_url,
                row,
                plan=plan,
                contact=contact,
                sent_at=now_utc,
            )
            _emit_event(
                FunnelEventName.FOLLOWUP_SENT,
                tenant_id,
                phone,
                {
                    "stage": plan.stage.value,
                    "lead_temperature": plan.temperature.value,
                    "followup_sequence": contact.sequence,
                },
            )
            sent += 1
        except Exception as exc:
            logger.error("commercial_followup send failed id=%s: %s", row.get("id"), exc)
            await release_followup_claim(db_url, str(row.get("id") or ""), retry_minutes=30)

    return sent


async def schedule_commercial_followups(
    db_url: str,
    tenant_id: str,
    *,
    now: Optional[datetime] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """Create or refresh scheduled commercial follow-up states."""
    if not db_url:
        return 0
    candidates = await get_followup_candidates(
        db_url,
        tenant_id,
        lookback_days=lookback_days,
    )
    scheduled = 0
    for candidate in candidates:
        if await is_followup_opted_out(db_url, tenant_id, candidate.phone):
            await upsert_blocked_followup(db_url, tenant_id, candidate, "opt_out")
            continue

        plan = build_followup_plan(
            messages=candidate.messages,
            stage=candidate.stage,
            contacts_already_sent=0,
            dry_or_resistant=candidate.dry_or_resistant,
        )
        if not plan.allowed or not plan.contacts:
            await upsert_blocked_followup(db_url, tenant_id, candidate, plan.reason)
            continue

        first = plan.contacts[0]
        next_send_at = _next_business_time(candidate.last_activity_at + timedelta(hours=first.delay_hours))
        await upsert_scheduled_followup(
            db_url,
            tenant_id,
            candidate,
            plan=plan,
            next_send_at=next_send_at,
        )
        _emit_event(
            FunnelEventName.FOLLOWUP_SCHEDULED,
            tenant_id,
            candidate.phone,
            {
                "stage": plan.stage.value,
                "lead_temperature": plan.temperature.value,
                "followup_sequence": first.sequence,
            },
        )
        scheduled += 1
    return scheduled


async def get_followup_candidates(
    db_url: str,
    tenant_id: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[FollowupCandidate]:
    """Return synthetic, PII-minimized candidates from persisted commercial signals."""
    if not db_url:
        return []
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                WITH commercial AS (
                    SELECT
                        ce.lead_phone AS phone,
                        MAX(ce.created_at) AS last_activity_at,
                        STRING_AGG(DISTINCT COALESCE(ce.action, ''), ' ') AS actions,
                        STRING_AGG(DISTINCT COALESCE(ce.buying_intent, ''), ' ') AS intents,
                        STRING_AGG(DISTINCT COALESCE(ce.sentiment, ''), ' ') AS sentiments,
                        STRING_AGG(DISTINCT COALESCE(ce.raw_signal->>'objection', ''), ' ') AS objections
                    FROM conversion_events ce
                    WHERE ce.tenant_id = $1
                      AND ce.created_at >= NOW() - ($2::int * INTERVAL '1 day')
                    GROUP BY ce.lead_phone
                )
                SELECT
                    c.phone,
                    c.last_activity_at,
                    c.actions,
                    c.intents,
                    c.sentiments,
                    c.objections,
                    COALESCE(l.objections::text, '') AS lead_objections,
                    o.status AS order_status,
                    o.billing_type AS billing_type
                FROM commercial c
                LEFT JOIN leads l
                  ON l.tenant_id = $1 AND l.phone = c.phone
                LEFT JOIN LATERAL (
                    SELECT status, billing_type
                    FROM orders o
                    WHERE o.tenant_id = $1
                      AND o.lead_phone = c.phone
                      AND o.status NOT IN ('paid', 'cancelled', 'refunded')
                    ORDER BY o.updated_at DESC NULLS LAST, o.created_at DESC
                    LIMIT 1
                ) o ON TRUE
                ORDER BY c.last_activity_at DESC
                """,
                tenant_id,
                int(lookback_days),
            )
        finally:
            await conn.close()
        return [_candidate_from_row(dict(row)) for row in rows]
    except Exception as exc:
        logger.error("get_followup_candidates failed tenant=%s: %s", tenant_id, exc)
        return []


async def is_followup_opted_out(db_url: str, tenant_id: str, phone: str) -> bool:
    """Check persistent opt-out in both current and legacy lead tables."""
    if not db_url or not phone:
        return False
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            value = await conn.fetchval(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM lead_profiles
                        WHERE tenant_id = $1 AND phone = $2 AND opt_out_at IS NOT NULL
                    )
                    OR EXISTS (
                        SELECT 1 FROM leads
                        WHERE tenant_id = $1 AND phone = $2 AND opt_out_at IS NOT NULL
                    )
                """,
                tenant_id,
                phone,
            )
        finally:
            await conn.close()
        return bool(value)
    except Exception as exc:
        logger.error("commercial_followup opt_out check failed tenant=%s: %s", tenant_id, exc)
        return False


async def upsert_scheduled_followup(
    db_url: str,
    tenant_id: str,
    candidate: FollowupCandidate,
    *,
    plan: FollowupPlan,
    next_send_at: datetime,
) -> None:
    """Persist scheduled state without resetting contacts already sent."""
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            INSERT INTO commercial_followups (
                tenant_id, phone, stage, status, contacts_sent, max_contacts,
                next_send_at, last_activity_at, last_temperature, context_messages,
                block_reason, updated_at
            )
            VALUES ($1, $2, $3, 'scheduled', 0, $4, $5, $6, $7, $8, NULL, NOW())
            ON CONFLICT (tenant_id, phone, stage) DO UPDATE SET
                max_contacts = EXCLUDED.max_contacts,
                next_send_at = CASE
                    WHEN commercial_followups.status = 'blocked'
                    THEN EXCLUDED.next_send_at
                    WHEN commercial_followups.status = 'scheduled'
                     AND commercial_followups.contacts_sent = 0
                     AND commercial_followups.next_send_at IS NULL
                    THEN EXCLUDED.next_send_at
                    ELSE commercial_followups.next_send_at
                END,
                last_activity_at = GREATEST(
                    COALESCE(commercial_followups.last_activity_at, EXCLUDED.last_activity_at),
                    EXCLUDED.last_activity_at
                ),
                last_temperature = EXCLUDED.last_temperature,
                context_messages = EXCLUDED.context_messages,
                status = CASE
                    WHEN commercial_followups.status = 'blocked'
                    THEN 'scheduled'
                    ELSE commercial_followups.status
                END,
                block_reason = NULL,
                updated_at = NOW()
            WHERE commercial_followups.status <> 'sending'
            """,
            tenant_id,
            candidate.phone,
            plan.stage.value,
            int(plan.max_contacts),
            next_send_at,
            candidate.last_activity_at,
            plan.temperature.value,
            candidate.messages,
        )
    finally:
        await conn.close()


async def upsert_blocked_followup(
    db_url: str,
    tenant_id: str,
    candidate: FollowupCandidate,
    reason: str,
) -> None:
    """Persist a durable blocked status for opt-out or medical risk."""
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            INSERT INTO commercial_followups (
                tenant_id, phone, stage, status, contacts_sent, max_contacts,
                last_activity_at, context_messages, block_reason, updated_at
            )
            VALUES ($1, $2, $3, 'blocked', 0, 0, $4, $5, $6, NOW())
            ON CONFLICT (tenant_id, phone, stage) DO UPDATE SET
                status = 'blocked',
                next_send_at = NULL,
                block_reason = EXCLUDED.block_reason,
                context_messages = EXCLUDED.context_messages,
                updated_at = NOW()
            """,
            tenant_id,
            candidate.phone,
            candidate.stage.value,
            candidate.last_activity_at,
            candidate.messages,
            reason,
        )
    finally:
        await conn.close()


async def claim_due_followups(
    db_url: str,
    tenant_id: str,
    *,
    now: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Atomically claim due rows. Claimed rows stay non-sendable across restart."""
    if not db_url:
        return []
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                WITH due AS (
                    SELECT id
                    FROM commercial_followups
                    WHERE tenant_id = $1
                      AND status = 'scheduled'
                      AND next_send_at IS NOT NULL
                      AND next_send_at <= $2
                    ORDER BY next_send_at ASC
                    LIMIT $3
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE commercial_followups f
                SET status = 'sending',
                    locked_at = NOW(),
                    updated_at = NOW()
                FROM due
                WHERE f.id = due.id
                RETURNING f.*
                """,
                tenant_id,
                _as_utc(now),
                int(limit),
            )
        finally:
            await conn.close()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("claim_due_followups failed tenant=%s: %s", tenant_id, exc)
        return []


async def mark_followup_sent(
    db_url: str,
    row: dict[str, Any],
    *,
    plan: FollowupPlan,
    contact: FollowupContact,
    sent_at: datetime,
) -> None:
    """Stamp success and schedule the next contact if the plan still allows one."""
    import asyncpg

    followup_id = str(row.get("id") or "")
    contacts_sent = int(row.get("contacts_sent") or 0)
    new_count = contacts_sent + 1
    next_plan = build_followup_plan(
        messages=str(row.get("context_messages") or ""),
        stage=plan.stage,
        contacts_already_sent=new_count,
    )
    next_send_at = _next_send_after_success(contact, next_plan, _as_utc(sent_at))
    status = "scheduled" if next_send_at else "completed"

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            UPDATE commercial_followups
            SET contacts_sent = $2,
                status = $3,
                next_send_at = $4,
                last_sent_at = $5,
                last_template_id = $6,
                last_temperature = $7,
                locked_at = NULL,
                block_reason = NULL,
                updated_at = NOW()
            WHERE id = $1 AND status = 'sending'
            """,
            followup_id,
            new_count,
            status,
            next_send_at,
            _as_utc(sent_at),
            contact.template_id,
            plan.temperature.value,
        )
    finally:
        await conn.close()


async def mark_followup_blocked(db_url: str, followup_id: str, reason: str) -> None:
    if not db_url or not followup_id:
        return
    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            UPDATE commercial_followups
            SET status = 'blocked',
                next_send_at = NULL,
                block_reason = $2,
                locked_at = NULL,
                updated_at = NOW()
            WHERE id = $1
            """,
            followup_id,
            reason,
        )
    finally:
        await conn.close()


async def release_followup_claim(db_url: str, followup_id: str, *, retry_minutes: int = 30) -> None:
    if not db_url or not followup_id:
        return
    import asyncpg

    retry_at = _next_business_time(datetime.now(timezone.utc) + timedelta(minutes=retry_minutes))
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            UPDATE commercial_followups
            SET status = 'scheduled',
                next_send_at = $2,
                locked_at = NULL,
                updated_at = NOW()
            WHERE id = $1 AND status = 'sending'
            """,
            followup_id,
            retry_at,
        )
    finally:
        await conn.close()


async def mark_followup_replied(
    db_url: str,
    tenant_id: str,
    phone: str,
    *,
    replied_at: Optional[datetime] = None,
) -> bool:
    """Mark the first reply after a commercial follow-up, best-effort."""
    if not db_url or not phone:
        return False
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                """
                UPDATE commercial_followups
                SET last_replied_at = $3,
                    updated_at = NOW()
                WHERE tenant_id = $1
                  AND phone = $2
                  AND last_sent_at IS NOT NULL
                  AND last_replied_at IS NULL
                  AND status IN ('scheduled', 'completed')
                RETURNING stage, last_temperature, contacts_sent
                """,
                tenant_id,
                phone,
                _as_utc(replied_at or datetime.now(timezone.utc)),
            )
        finally:
            await conn.close()
        if not row:
            return False
        _emit_event(
            FunnelEventName.FOLLOWUP_REPLIED,
            tenant_id,
            phone,
            {
                "stage": row["stage"],
                "lead_temperature": row["last_temperature"],
                "followup_sequence": row["contacts_sent"],
            },
        )
        return True
    except Exception as exc:
        logger.warning("mark_followup_replied failed tenant=%s: %s", tenant_id, exc)
        return False


def _candidate_from_row(row: dict[str, Any]) -> FollowupCandidate:
    order_status = str(row.get("order_status") or "")
    if order_status == "payment_link_created":
        stage = FollowupStage.POST_LINK
    elif order_status in {"draft", "checkout_incomplete"}:
        stage = FollowupStage.CHECKOUT_INCOMPLETE
    else:
        stage = FollowupStage.POST_OFFER

    return FollowupCandidate(
        phone=str(row.get("phone") or ""),
        stage=stage,
        messages=_messages_from_signal_row(row),
        last_activity_at=_as_utc(row.get("last_activity_at") or datetime.now(timezone.utc)),
        dry_or_resistant=_dry_or_resistant(row),
    )


def _messages_from_signal_row(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "")
        for key in (
            "actions",
            "intents",
            "sentiments",
            "objections",
            "lead_objections",
            "order_status",
            "billing_type",
        )
    ).lower()
    signals: list[str] = [text]
    if "health_risk" in text or "escalate_human" in text:
        signals.append("tomo remedio tive reacao alergia")
    if "send_payment_link" in text or "payment_link_created" in text:
        signals.append("quero comprar manda o link pix")
    if "medium" in text or "price" in text or "preco" in text:
        signals.append("qual o valor preco")
    if "draft" in text or "checkout" in text:
        signals.append("cpf cep endereco")
    if "frete" in text:
        signals.append("frete entrega")
    return " ".join(signals).strip()


def _dry_or_resistant(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("objections", "lead_objections")).lower()
    return any(term in text for term in ("depois", "pensar", "nao sei", "não sei"))


def _next_send_after_success(
    sent_contact: FollowupContact,
    next_plan: FollowupPlan,
    sent_at: datetime,
) -> Optional[datetime]:
    if not next_plan.allowed or not next_plan.contacts:
        return None
    next_contact = next_plan.contacts[0]
    delta_hours = max(1, int(next_contact.delay_hours) - int(sent_contact.delay_hours))
    return _next_business_time(sent_at + timedelta(hours=delta_hours))


def _next_business_time(value: datetime) -> datetime:
    local = _as_utc(value).astimezone(BRT)
    if local.hour < COMMERCIAL_START_HOUR:
        local = local.replace(
            hour=COMMERCIAL_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    elif local.hour >= COMMERCIAL_END_HOUR:
        local = (local + timedelta(days=1)).replace(
            hour=COMMERCIAL_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    return local.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _send_whatsapp(whatsapp_tool: Any, *, phone: str, text: str, session_id: str) -> bool:
    if hasattr(whatsapp_tool, "send_message"):
        result = await whatsapp_tool.send_message(phone=phone, text=text, session_id=session_id)
        return bool(getattr(result, "success", True))
    result = await whatsapp_tool(phone=phone, message=text)
    return bool(getattr(result, "success", True))


def _emit_event(
    event: FunnelEventName,
    tenant_id: str,
    phone: str,
    metadata: dict[str, Any],
) -> None:
    try:
        funnel_event = build_funnel_event(
            tenant_id=tenant_id,
            session_id=phone,
            event=event,
            metadata=metadata,
        )
        _obs.emit_funnel_event(funnel_event)
    except Exception:
        pass
