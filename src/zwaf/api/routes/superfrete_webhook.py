"""SuperFrete tracking webhook."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from zwaf.memory.order_store import record_superfrete_tracking_event

logger = logging.getLogger("zwaf.api.superfrete_webhook")

router = APIRouter()

_STATUS_MAP = {
    "order.created": "created",
    "order.released": "released",
    "order.generated": "generated",
    "order.posted": "posted",
    "order.delivered": "delivered",
    "order.cancelled": "cancelled",
}


@router.post("/shipping/superfrete/{tenant_id}")
async def receive_superfrete_webhook(
    tenant_id: str,
    request: Request,
    x_me_signature: str = Header(default="", alias="X-ME-Signature"),
) -> dict[str, str]:
    body_bytes = await request.body()
    if not _verify_signature(body_bytes, x_me_signature):
        logger.warning("Invalid SuperFrete webhook signature for tenant %s", tenant_id)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = str(body.get("event") or "")
    status = _STATUS_MAP.get(event)
    if not status:
        return {"status": "ignored", "event": event}

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    provider_order_id = str(data.get("id") or body.get("id") or "")
    tracking_code = str(data.get("tracking") or data.get("tracking_code") or "")
    event_id = _provider_event_id(body, event, provider_order_id, tracking_code)
    result = await record_superfrete_tracking_event(
        tenant_id=tenant_id,
        event_id=event_id,
        event_type=event,
        provider_order_id=provider_order_id,
        tracking_code=tracking_code,
        status=status,
        event_at=_event_at(data, status),
        raw_payload_redacted=_redacted_payload(event, data),
    )
    return {"status": result}


def _verify_signature(body: bytes, received_signature: str) -> bool:
    secret = os.getenv("SUPERFRETE_WEBHOOK_SECRET", "")
    if not secret:
        return os.getenv("ENV", "").lower() != "production"
    if not received_signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(_signature_value(received_signature), expected)


def _signature_value(value: str) -> str:
    cleaned = (value or "").strip()
    if "=" in cleaned:
        return cleaned.rsplit("=", 1)[-1].strip()
    return cleaned


def _provider_event_id(
    body: dict[str, Any],
    event: str,
    provider_order_id: str,
    tracking_code: str,
) -> str:
    explicit_id = str(body.get("id") or body.get("event_id") or "").strip()
    if explicit_id:
        return explicit_id
    return f"{event}:{provider_order_id}:{tracking_code}".strip(":")


def _event_at(data: dict[str, Any], status: str) -> datetime:
    field = {
        "created": "created_at",
        "released": "paid_at",
        "generated": "generated_at",
        "posted": "posted_at",
        "delivered": "delivered_at",
        "cancelled": "canceled_at",
    }.get(status, "created_at")
    raw = data.get(field) or data.get("updated_at") or data.get("created_at")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _redacted_payload(event: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "id": data.get("id"),
        "status": data.get("status"),
        "tracking": data.get("tracking") or data.get("tracking_code"),
        "tracking_url": data.get("tracking_url"),
        "created_at": data.get("created_at"),
        "paid_at": data.get("paid_at"),
        "generated_at": data.get("generated_at"),
        "posted_at": data.get("posted_at"),
        "delivered_at": data.get("delivered_at"),
        "canceled_at": data.get("canceled_at"),
    }
