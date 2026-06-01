"""Asaas payment webhook unit tests."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zwaf.api.routes import payment_webhook


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("ASAAS_WEBHOOK_AUTH_TOKEN", "webhook-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    app.include_router(payment_webhook.router)
    return TestClient(app)


def test_payment_received_webhook_is_accepted_without_db(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_123",
                "value": 165.90,
                "externalReference": "livia-raiz-vital:5511999990001:new-woman-1:nw-001",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted_no_db"}


def test_payment_webhook_rejects_invalid_token(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "wrong-token"},
        json={"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}},
    )

    assert response.status_code == 401


def test_payment_webhook_ignores_unknown_event(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={"event": "CUSTOMER_CREATED", "payment": {"id": "pay_123"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "CUSTOMER_CREATED"}


def test_asaas_event_helpers_parse_reference_and_amount():
    phone, product_id = payment_webhook._parse_external_reference(
        "livia-raiz-vital:5511999990001:new-woman-1:nw-001"
    )

    assert phone == "5511999990001"
    assert product_id == "new-woman-1"
    assert payment_webhook._amount_to_cents(165.90) == 16590
