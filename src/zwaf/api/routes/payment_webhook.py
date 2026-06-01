"""
Payment Webhook - POST /v1/webhook/payment/{tenant_id}

Recebe notificacoes do Asaas, verifica auth token do webhook, registra em
payment_events e atualiza purchase_history do lead quando PAID.

Isso aciona o FidelizacaoScheduler 30 dias depois.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger("zwaf.api.payment_webhook")

router = APIRouter()

_PAID_EVENTS = {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}

_STATUS_MAP = {
    "PAYMENT_CREATED": "PENDING",
    "PAYMENT_UPDATED": "PENDING",
    "PAYMENT_CONFIRMED": "PAID",
    "PAYMENT_RECEIVED": "PAID",
    "PAYMENT_OVERDUE": "OVERDUE",
    "PAYMENT_REFUNDED": "REFUNDED",
    "PAYMENT_PARTIALLY_REFUNDED": "PARTIALLY_REFUNDED",
    "PAYMENT_DELETED": "CANCELLED",
    "PAYMENT_BANK_SLIP_CANCELLED": "CANCELLED",
}


def _verify_auth_token(received_token: str, expected_token: str) -> bool:
    """Verifica auth token configurado no webhook Asaas."""
    if not expected_token:
        return True  # Dev/test mode sem token configurado.
    return received_token == expected_token


@router.post("/payment/{tenant_id}")
async def receive_payment_webhook(
    tenant_id: str,
    request: Request,
    asaas_access_token: str = Header(default="", alias="asaas-access-token"),
    x_asaas_webhook_token: str = Header(default=""),
) -> dict:
    """
    Recebe evento de cobranca do Asaas para um tenant.

    Eventos tratados:
    - PAYMENT_CONFIRMED / PAYMENT_RECEIVED -> PAID
    - PAYMENT_OVERDUE -> OVERDUE
    - PAYMENT_REFUNDED / PAYMENT_PARTIALLY_REFUNDED -> REFUNDED/PARTIALLY_REFUNDED
    - PAYMENT_DELETED / PAYMENT_BANK_SLIP_CANCELLED -> CANCELLED
    """
    body_bytes = await request.body()

    expected_token = os.getenv("ASAAS_WEBHOOK_AUTH_TOKEN", "")
    received_token = asaas_access_token or x_asaas_webhook_token
    if not _verify_auth_token(received_token, expected_token):
        logger.warning("Invalid Asaas webhook token for tenant %s", tenant_id)
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event", "")
    payment = body.get("payment", {})
    status = _STATUS_MAP.get(event)
    if not status:
        return {"status": "ignored", "event": event}

    payment_id = payment.get("id", "")
    external_reference = payment.get("externalReference", "")
    lead_phone, product_id = _parse_external_reference(external_reference)
    amount_cents = _amount_to_cents(payment.get("value", 0))

    logger.info(
        "Payment webhook received",
        extra={
            "tenant_id": tenant_id,
            "event": event,
            "payment_id": payment_id,
            "status": status,
        },
    )

    db_url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "")
    if not db_url:
        logger.warning("DATABASE_URL not set - payment event not persisted")
        return {"status": "accepted_no_db"}

    try:
        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                INSERT INTO payment_events
                    (tenant_id, payment_id, lead_phone, product_id, amount_cents, status, raw_payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                payment_id,
                lead_phone,
                product_id,
                amount_cents,
                status,
                json.dumps(body),
            )

            if event in _PAID_EVENTS and lead_phone:
                purchase_entry = json.dumps(
                    [
                        {
                            "payment_id": payment_id,
                            "product_id": product_id,
                            "amount_cents": amount_cents,
                        }
                    ]
                )
                await conn.execute(
                    """
                    INSERT INTO leads (tenant_id, phone, purchase_history, updated_at)
                    VALUES ($1, $2, $3::jsonb, NOW())
                    ON CONFLICT (tenant_id, phone) DO UPDATE
                    SET
                        purchase_history = leads.purchase_history || $3::jsonb,
                        updated_at = NOW()
                    """,
                    tenant_id,
                    lead_phone,
                    purchase_entry,
                )
                logger.info(
                    "Lead purchase_history updated",
                    extra={"tenant_id": tenant_id, "phone_tail": lead_phone[-4:]},
                )
        finally:
            await conn.close()
    except Exception as e:
        logger.error("Failed to persist payment event: %s", e)
        # Retorna 200 para o Asaas nao reenviar indefinidamente; log e segue.
        return {"status": "accepted_db_error"}

    return {"status": "accepted"}


def _parse_external_reference(reference: str) -> tuple[str, str]:
    """Extrai phone e product_id de tenant:phone:product_id:external_id."""
    parts = (reference or "").split(":")
    if len(parts) >= 3:
        return parts[1], parts[2]
    return "", ""


def _amount_to_cents(value: Optional[Any]) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0
