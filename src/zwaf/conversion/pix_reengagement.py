"""PIX re-engagement: remind leads with pending PIX before the charge expires.

story-051 — hourly job checks orders with billing_type=PIX, status=payment_link_created,
pix_due_date within the next 24 hours, and reengagement_sent_at IS NULL.
One reminder per order, respects opt-out, no LGPD data in logs.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Optional

logger = logging.getLogger("zwaf.conversion.pix_reengagement")


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")


async def get_pending_pix_orders(
    db_url: str,
    tenant_id: str,
    *,
    lookahead_days: int = 1,
) -> list[dict[str, Any]]:
    """Return PIX orders expiring within lookahead_days that haven't been re-engaged yet."""
    if not db_url:
        return []
    cutoff = date.today() + timedelta(days=lookahead_days)
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT o.id, o.lead_phone, o.total_cents, o.pix_due_date,
                       o.asaas_payment_url
                FROM orders o
                WHERE o.tenant_id = $1
                  AND o.billing_type = 'PIX'
                  AND o.status = 'payment_link_created'
                  AND o.pix_due_date IS NOT NULL
                  AND o.pix_due_date <= $2
                  AND o.reengagement_sent_at IS NULL
                ORDER BY o.pix_due_date ASC
                """,
                tenant_id,
                cutoff,
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_pending_pix_orders failed tenant=%s: %s", tenant_id, exc)
        return []


async def is_opted_out(db_url: str, tenant_id: str, phone: str) -> bool:
    """Return True if the lead has opted out of marketing messages."""
    if not db_url or not phone:
        return False
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            opt_out = await conn.fetchval(
                """
                SELECT opt_out_at IS NOT NULL
                FROM lead_profiles
                WHERE tenant_id = $1 AND phone = $2
                """,
                tenant_id,
                phone,
            )
        finally:
            await conn.close()
        return bool(opt_out)
    except Exception as exc:
        logger.error("is_opted_out check failed tenant=%s: %s", tenant_id, exc)
        return False


async def mark_reengagement_sent(db_url: str, order_id: str) -> None:
    """Stamp reengagement_sent_at so we never send a second reminder."""
    if not db_url or not order_id:
        return
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                UPDATE orders
                SET reengagement_sent_at = NOW(), updated_at = NOW()
                WHERE id = $1 AND reengagement_sent_at IS NULL
                """,
                order_id,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("mark_reengagement_sent failed order=%s: %s", order_id, exc)


def build_reengagement_message(
    total_cents: int,
    pix_due_date: Optional[date],
    payment_url: Optional[str] = None,
    lead_memory: Optional[dict] = None,
) -> str:
    """Build the WhatsApp re-engagement message for an expiring PIX charge.

    Optionally personalized with lead memory (story-066). LGPD-safe: no PII in message.
    """
    price = _format_brl(total_cents)
    due_str = (
        pix_due_date.strftime("%d/%m") if pix_due_date else "hoje"
    )

    # Personalization based on memory (LGPD guardrails: no PII in message)
    personalization = ""
    if lead_memory:
        objections = lead_memory.get("objections") or []
        has_price_objection = any(
            "preco" in str(o).lower()
            or "valor" in str(o).lower()
            or "caro" in str(o).lower()
            for o in objections
        )
        if has_price_objection and total_cents > 0:
            per_day = total_cents / 100 / 30
            personalization = f" Vale menos de R$ {per_day:.2f} por dia."
        elif lead_memory.get("primary_symptom"):
            # Never expose the symptom itself -- use a generic reference
            personalization = " Ainda dá tempo de cuidar do que você está sentindo."

    msg = (
        f"Oi! Sou a Lívia, da Raiz Vital — assistente virtual 🌿\n\n"
        f"Seu Pix de {price} vence em {due_str}. "
        f"Ainda dá tempo de garantir seu New Woman!{personalization} "
        "É só copiar o código Pix que te enviamos e colar no app do seu banco. 💚\n\n"
        "Qualquer dúvida, é só responder aqui."
    )
    return msg


async def run_pix_reengagement_job(
    db_url: str,
    tenant_id: str,
    whatsapp_tool: Any,
    *,
    lookahead_days: int = 1,
) -> int:
    """Run one cycle of the PIX re-engagement job. Returns number of messages sent."""
    if not db_url or not whatsapp_tool:
        return 0

    orders = await get_pending_pix_orders(db_url, tenant_id, lookahead_days=lookahead_days)
    if not orders:
        return 0

    sent = 0
    for order in orders:
        phone: str = str(order.get("lead_phone") or "")
        order_id: str = str(order.get("id") or "")
        if not phone or not order_id:
            continue

        opted_out = await is_opted_out(db_url, tenant_id, phone)
        if opted_out:
            logger.info(
                "pix_reengagement skipped opted_out order=%s",
                order_id,
            )
            continue

        # Fetch lead memory for personalization (story-066) -- best-effort, LGPD-safe
        lead_memory = None
        try:
            from zwaf.memory.lead_store import get_lead_memory
            lead_memory = await get_lead_memory(phone=phone, tenant_id=tenant_id)
        except Exception:
            pass  # memory is optional -- never block the send

        message = build_reengagement_message(
            total_cents=int(order.get("total_cents") or 0),
            pix_due_date=order.get("pix_due_date"),
            payment_url=order.get("asaas_payment_url"),
            lead_memory=lead_memory,
        )
        try:
            await whatsapp_tool(phone=phone, message=message)
            await mark_reengagement_sent(db_url, order_id)
            sent += 1
            logger.info("pix_reengagement sent order=%s", order_id)
        except Exception as exc:
            logger.error("pix_reengagement send failed order=%s: %s", order_id, exc)

    return sent


def _format_brl(price_cents: int) -> str:
    formatted = f"{price_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"
