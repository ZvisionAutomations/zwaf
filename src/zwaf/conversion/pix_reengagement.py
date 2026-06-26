"""PIX re-engagement: remind leads with pending PIX before the charge expires.

story-051 — hourly job checks orders with billing_type=PIX, status=payment_link_created,
pix_due_date within the next 24 hours, and reengagement_sent_at IS NULL.
One reminder per order, respects opt-out, no LGPD data in logs.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Optional

from zwaf.db.dsn import normalize_dsn
from zwaf.security.pii import decrypt_pii

logger = logging.getLogger("zwaf.conversion.pix_reengagement")


def _db_url() -> str:
    return normalize_dsn(os.getenv("DATABASE_URL"))


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


async def get_lead_reengagement_memory(
    db_url: str,
    tenant_id: str,
    phone: str,
) -> Optional[dict[str, Any]]:
    """Read lead memory for Pix re-engagement. PII is decrypted only in runtime."""
    if not db_url or not phone:
        return None
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                """
                SELECT primary_symptom_enc, memory_summary_enc, objections, next_best_action
                FROM leads
                WHERE tenant_id = $1 AND phone = $2
                """,
                tenant_id,
                phone,
            )
        finally:
            await conn.close()
        if not row:
            return None

        objections = row["objections"]
        if isinstance(objections, str):
            try:
                objections = json.loads(objections)
            except (TypeError, ValueError):
                objections = []

        return {
            "primary_symptom": (
                decrypt_pii(row["primary_symptom_enc"]) if row["primary_symptom_enc"] else ""
            ),
            "memory_summary": (
                decrypt_pii(row["memory_summary_enc"]) if row["memory_summary_enc"] else ""
            ),
            "objections": objections or [],
            "next_best_action": row["next_best_action"] or "",
        }
    except Exception as exc:
        logger.warning("get_lead_reengagement_memory failed tenant=%s: %s", tenant_id, exc)
        return None


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
    lead_memory: Optional[dict[str, Any]] = None,
) -> str:
    """Build the WhatsApp re-engagement message for an expiring PIX charge."""
    price = _format_brl(total_cents)
    due_str = (
        pix_due_date.strftime("%d/%m") if pix_due_date else "hoje"
    )
    msg = (
        f"Oi! Sou a Lívia, da Raiz Vital — assistente virtual 🌿\n\n"
        f"Seu Pix de {price} vence em {due_str}. "
        "Ainda dá tempo de garantir seu New Woman! "
        "É só copiar o código Pix que te enviamos e colar no app do seu banco. 💚\n\n"
        "Qualquer dúvida, é só responder aqui."
    )
    if _has_reengagement_memory(lead_memory):
        msg = _personalized_reengagement_message(
            total_cents=total_cents,
            pix_due_date=pix_due_date,
            payment_url=payment_url,
            lead_memory=lead_memory or {},
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

        message = build_reengagement_message(
            total_cents=int(order.get("total_cents") or 0),
            pix_due_date=order.get("pix_due_date"),
            payment_url=order.get("asaas_payment_url"),
            lead_memory=await get_lead_reengagement_memory(db_url, tenant_id, phone),
        )
        try:
            await _send_whatsapp(whatsapp_tool, phone=phone, message=message)
            await mark_reengagement_sent(db_url, order_id)
            sent += 1
            logger.info("pix_reengagement sent order=%s", order_id)
        except Exception as exc:
            logger.error("pix_reengagement send failed order=%s: %s", order_id, exc)

    return sent


def _format_brl(price_cents: int) -> str:
    formatted = f"{price_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def _personalized_reengagement_message(
    *,
    total_cents: int,
    pix_due_date: Optional[date],
    payment_url: Optional[str],
    lead_memory: dict[str, Any],
) -> str:
    price = _format_brl(total_cents)
    due_str = pix_due_date.strftime("%d/%m") if pix_due_date else "hoje"
    symptom = _short_memory_text(str(lead_memory.get("primary_symptom") or ""))
    objections = _objection_terms(lead_memory.get("objections"))

    msg = (
        f"Oi! Sou a Livia, da Raiz Vital - assistente virtual\n\n"
        f"Seu Pix de {price} vence em {due_str}."
    )
    if symptom:
        msg += f" Lembrei que voce comentou sobre {symptom}; se ainda faz sentido, seu pedido ficou separado."
    else:
        msg += " Seu pedido do New Woman ficou separado."

    if _has_price_objection(objections):
        msg += (
            f"\n\nSe o ponto era valor, esse pedido fica em cerca de {_format_brl(total_cents // 30)} "
            "por dia considerando 30 dias, sem mudar o valor combinado."
        )
    elif _has_safety_objection(objections):
        msg += (
            "\n\nSe a duvida for seguranca, me responde aqui antes de pagar. "
            "Se envolver remedio, reacao ou orientacao medica, eu te direciono para o Fernando."
        )
    else:
        msg += "\n\nSe travou em alguma duvida, me responde aqui que eu te ajudo com calma."

    msg += (
        "\n\nSe quiser seguir, e so copiar o codigo Pix que te enviamos e colar no app do seu banco."
    )
    return msg


def _has_reengagement_memory(memory: Optional[dict[str, Any]]) -> bool:
    if not memory:
        return False
    return bool(
        memory.get("primary_symptom")
        or memory.get("next_best_action")
        or _objection_terms(memory.get("objections"))
    )


def _short_memory_text(value: str) -> str:
    cleaned = " ".join(str(value or "").replace("\n", " ").split())
    return cleaned[:80]


def _objection_terms(value: Any) -> set[str]:
    if not value:
        return set()
    items = [value] if isinstance(value, str) else list(value)
    normalized: set[str] = set()
    for item in items:
        text = str(item or "").lower()
        normalized.add(text)
        if "preco" in text or "preço" in text or "caro" in text or "valor" in text:
            normalized.add("price")
        if "segur" in text or "medo" in text or "remedio" in text or "remédio" in text:
            normalized.add("safety")
    return normalized


def _has_price_objection(objections: set[str]) -> bool:
    return "price" in objections


def _has_safety_objection(objections: set[str]) -> bool:
    return "safety" in objections


async def _send_whatsapp(whatsapp_tool: Any, *, phone: str, message: str) -> None:
    if hasattr(whatsapp_tool, "send_message"):
        await whatsapp_tool.send_message(phone=phone, text=message, session_id=f"pix:{phone}")
        return
    await whatsapp_tool(phone=phone, message=message)
