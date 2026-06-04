"""SuperFrete webhook unit tests."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zwaf.api.routes import superfrete_webhook


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(superfrete_webhook.router)
    return TestClient(app)


def test_superfrete_webhook_accepts_event_without_db_in_development(monkeypatch):
    monkeypatch.delenv("SUPERFRETE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    async def fake_record(**kwargs):
        return "accepted_no_db"

    monkeypatch.setattr(superfrete_webhook, "record_superfrete_tracking_event", fake_record)
    response = _client().post(
        "/shipping/superfrete/livia-raiz-vital",
        json={"event": "order.generated", "data": {"id": "sf_123", "tracking": "DG123BR"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted_no_db"}


def test_superfrete_webhook_rejects_invalid_signature_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SUPERFRETE_WEBHOOK_SECRET", "webhook-secret")

    response = _client().post(
        "/shipping/superfrete/livia-raiz-vital",
        headers={"X-ME-Signature": "wrong"},
        json={"event": "order.delivered", "data": {"id": "sf_123"}},
    )

    assert response.status_code == 401


def test_superfrete_webhook_validates_hmac_and_redacts_payload(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SUPERFRETE_WEBHOOK_SECRET", "webhook-secret")
    captured: dict = {}

    async def fake_record(**kwargs):
        captured.update(kwargs)
        return "accepted"

    monkeypatch.setattr(superfrete_webhook, "record_superfrete_tracking_event", fake_record)
    body = {
        "event": "order.delivered",
        "data": {
            "id": "sf_123",
            "status": "delivered",
            "tracking": "DG048745602BR",
            "tracking_url": "https://rastreio.superfrete.com/#/tracking/x",
            "delivered_at": "2026-06-03T12:00:00+00:00",
            "recipient_document": "doc-redacted",
        },
    }
    body_bytes = json.dumps(body).encode("utf-8")
    signature = hmac.new(b"webhook-secret", body_bytes, hashlib.sha256).hexdigest()

    response = _client().post(
        "/shipping/superfrete/livia-raiz-vital",
        headers={"X-ME-Signature": signature},
        content=body_bytes,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert captured["tenant_id"] == "livia-raiz-vital"
    assert captured["event_type"] == "order.delivered"
    assert captured["provider_order_id"] == "sf_123"
    assert captured["tracking_code"] == "DG048745602BR"
    assert captured["status"] == "delivered"
    assert "recipient_document" not in captured["raw_payload_redacted"]


def test_superfrete_webhook_ignores_unknown_event(monkeypatch):
    monkeypatch.delenv("SUPERFRETE_WEBHOOK_SECRET", raising=False)
    response = _client().post(
        "/shipping/superfrete/livia-raiz-vital",
        json={"event": "order.unknown", "data": {"id": "sf_123"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "order.unknown"}


def test_superfrete_signature_accepts_prefixed_value(monkeypatch):
    monkeypatch.setenv("SUPERFRETE_WEBHOOK_SECRET", "webhook-secret")
    body = b'{"event":"order.posted"}'
    digest = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()

    assert superfrete_webhook._verify_signature(body, f"sha256={digest}") is True
