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


def test_payment_webhook_rejects_missing_token_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ASAAS_WEBHOOK_AUTH_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(payment_webhook.router)
    client = TestClient(app)

    response = client.post(
        "/payment/livia-raiz-vital",
        json={"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}},
    )

    assert response.status_code == 401


def test_payment_webhook_rejects_tenant_mismatch(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_123",
                "externalReference": "caio-alpha-pulse:5511999990001:alpha-pulse-1:ap-001",
            },
        },
    )

    assert response.status_code == 400


def test_payment_webhook_duplicate_event_does_not_update_purchase_history(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://zwaf:test@postgres:5432/zwaf")

    class FakeConnection:
        def __init__(self):
            self.calls: list[str] = []

        async def execute(self, query, *args):
            self.calls.append(query)
            if "INSERT INTO payment_events" in query:
                return "INSERT 0 0"
            raise AssertionError("duplicate webhook should not update leads")

        async def close(self):
            return None

    fake_conn = FakeConnection()

    async def fake_connect(_db_url):
        return fake_conn

    monkeypatch.setattr(payment_webhook.asyncpg, "connect", fake_connect)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={
            "id": "evt_123",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_123",
                "value": 165.90,
                "externalReference": "livia-raiz-vital:5511999990001:new-woman-1:nw-001",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted_duplicate"}
    assert len(fake_conn.calls) == 1


def test_asaas_event_helpers_parse_reference_and_amount():
    phone, product_id = payment_webhook._parse_external_reference(
        "livia-raiz-vital:5511999990001:new-woman-1:nw-001"
    )

    assert phone == "5511999990001"
    assert product_id == "new-woman-1"
    assert (
        payment_webhook._reference_tenant("livia-raiz-vital:5511999990001:new-woman-1:nw-001")
        == "livia-raiz-vital"
    )
    assert payment_webhook._amount_to_cents(165.90) == 16590


def test_provider_event_id_prefers_asaas_event_id():
    body = {"id": "evt_123", "event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}}

    assert payment_webhook._provider_event_id(body, "PAYMENT_RECEIVED", "pay_123") == "evt_123"


def test_provider_event_id_falls_back_to_event_and_payment_id():
    assert (
        payment_webhook._provider_event_id({}, "PAYMENT_RECEIVED", "pay_123")
        == "PAYMENT_RECEIVED:pay_123"
    )


def test_inserted_parses_asyncpg_insert_status():
    assert payment_webhook._inserted("INSERT 0 1") is True
    assert payment_webhook._inserted("INSERT 0 0") is False


def test_payment_paid_event_marks_order_paid(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://zwaf:test@postgres:5432/zwaf")

    class FakeConnection:
        def __init__(self):
            self.calls: list[str] = []

        async def execute(self, query, *args):
            self.calls.append(query)
            if "INSERT INTO payment_events" in query:
                return "INSERT 0 1"
            return "UPDATE 1"

        async def close(self):
            return None

    fake_conn = FakeConnection()

    async def fake_connect(_db_url):
        return fake_conn

    monkeypatch.setattr(payment_webhook.asyncpg, "connect", fake_connect)

    response = client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={
            "id": "evt_paid_1",
            "event": "PAYMENT_RECEIVED",
            "payment": {
                "id": "pay_123",
                "value": 165.90,
                "externalReference": "livia-raiz-vital:5511999990001:new-woman-1:nw-001",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert any("UPDATE orders" in q and "status = 'paid'" in q for q in fake_conn.calls)
