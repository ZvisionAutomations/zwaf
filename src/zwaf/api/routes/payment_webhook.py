"""
Payment Webhook — POST /v1/webhook/payment/{tenant_id}

Recebe notificacoes do Abacate Pay, verifica assinatura HMAC-SHA256,
registra em payment_events e atualiza purchase_history do lead quando PAID.

Isso aciona o FidelizacaoScheduler 30 dias depois.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request

logger = logging.getLogger("zwaf.api.payment_webhook")

router = APIRouter()


def _verify_signature(body_bytes: bytes, signature: str, secret: str) -> bool:
    """Verifica assinatura HMAC-SHA256 do Abacate Pay."""
    if not secret:
        return True  # Dev mode sem secret configurado
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/payment/{tenant_id}")
async def receive_payment_webhook(
    tenant_id: str,
    request: Request,
    x_abacate_signature: str = Header(default=""),
) -> dict:
    """
    Recebe evento do Abacate Pay para um tenant.

    Eventos tratados:
    - billing.paid   -> PAID   -> registra + atualiza lead purchase_history
    - billing.expired -> EXPIRED -> registra
    - billing.refunded -> REFUNDED -> registra
    """
    body_bytes = await request.body()

    # Verificacao HMAC
    secret = os.getenv("ABACATE_PAY_WEBHOOK_SECRET", "")
    if not _verify_signature(body_bytes, x_abacate_signature, secret):
        logger.warning("Invalid Abacate Pay signature for tenant %s", tenant_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event", "")
    data = body.get("data", {})

    status_map = {
        "billing.paid": "PAID",
        "billing.expired": "EXPIRED",
        "billing.refunded": "REFUNDED",
    }

    if event not in status_map:
        return {"status": "ignored", "event": event}

    payment_id = data.get("id", "")
    lead_phone = (data.get("customer") or {}).get("cellphone", "")
    products = data.get("products") or []
    product_id = products[0].get("externalId", "") if products else ""
    amount_cents = data.get("amount", 0)
    status = status_map[event]

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
        logger.warning("DATABASE_URL not set — payment event not persisted")
        return {"status": "accepted_no_db"}

    try:
        conn = await asyncpg.connect(db_url)
        try:
            # Registrar evento de pagamento
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

            # Se pago: atualizar purchase_history do lead (usado pelo FidelizacaoScheduler)
            if status == "PAID" and lead_phone:
                purchase_entry = json.dumps([{
                    "payment_id": payment_id,
                    "product_id": product_id,
                    "amount_cents": amount_cents,
                }])
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
        # Retorna 200 para o Abacate Pay nao reenviar — log e segue
        return {"status": "accepted_db_error"}

    return {"status": "accepted"}