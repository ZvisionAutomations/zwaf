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


# ---------------------------------------------------------------------------
# Inventory effects (story-034)
# ---------------------------------------------------------------------------


class _Tx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class FakeTxConnection:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, query, *args):
        self.calls.append(query)
        if "INSERT INTO payment_events" in query:
            return "INSERT 0 1"
        return "UPDATE 1"

    def transaction(self):
        return _Tx(self)

    async def close(self):
        return None


def _post_event(client, *, event, event_id):
    return client.post(
        "/payment/livia-raiz-vital",
        headers={"asaas-access-token": "webhook-token"},
        json={
            "id": event_id,
            "event": event,
            "payment": {
                "id": "pay_123",
                "value": 165.90,
                "externalReference": "livia-raiz-vital:5511999990001:new-woman-1:nw-001",
            },
        },
    )


def _wire_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://zwaf:test@postgres:5432/zwaf")
    fake_conn = FakeTxConnection()

    async def fake_connect(_db_url):
        return fake_conn

    monkeypatch.setattr(payment_webhook.asyncpg, "connect", fake_connect)
    return fake_conn


def test_paid_event_confirms_inventory_reservation(monkeypatch):
    client = _client(monkeypatch)
    _wire_db(monkeypatch)
    seen = {}

    async def fake_confirm(conn, *, tenant_id, payment_id):
        seen["confirm"] = (tenant_id, payment_id)
        return "confirmed"

    monkeypatch.setattr(payment_webhook, "confirm_sale_for_payment_conn", fake_confirm)

    response = _post_event(client, event="PAYMENT_RECEIVED", event_id="evt_paid")

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert seen["confirm"] == ("livia-raiz-vital", "pay_123")


def test_cancelled_event_releases_inventory_reservation(monkeypatch):
    client = _client(monkeypatch)
    _wire_db(monkeypatch)
    seen = {}

    async def fake_release(conn, *, tenant_id, payment_id, reason="cancelled before payment"):
        seen["release"] = (tenant_id, payment_id)
        return True

    monkeypatch.setattr(payment_webhook, "release_reservation_for_payment_conn", fake_release)

    response = _post_event(client, event="PAYMENT_DELETED", event_id="evt_cancel")

    assert response.status_code == 200
    assert seen["release"] == ("livia-raiz-vital", "pay_123")


def test_refund_event_marks_review_without_restocking(monkeypatch):
    client = _client(monkeypatch)
    _wire_db(monkeypatch)
    seen = {}

    async def fake_refund(conn, *, tenant_id, payment_id):
        seen["refund"] = (tenant_id, payment_id)
        return True

    monkeypatch.setattr(payment_webhook, "mark_refund_review_conn", fake_refund)

    response = _post_event(client, event="PAYMENT_REFUNDED", event_id="evt_refund")

    assert response.status_code == 200
    assert seen["refund"] == ("livia-raiz-vital", "pay_123")


def test_duplicate_paid_event_does_not_confirm_inventory_twice(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://zwaf:test@postgres:5432/zwaf")
    confirms = []

    class DupConnection(FakeTxConnection):
        async def execute(self, query, *args):
            self.calls.append(query)
            if "INSERT INTO payment_events" in query:
                return "INSERT 0 0"  # duplicate
            return "UPDATE 1"

    fake_conn = DupConnection()

    async def fake_connect(_db_url):
        return fake_conn

    async def fake_confirm(conn, *, tenant_id, payment_id):
        confirms.append(payment_id)
        return "confirmed"

    monkeypatch.setattr(payment_webhook.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(payment_webhook, "confirm_sale_for_payment_conn", fake_confirm)

    response = _post_event(client, event="PAYMENT_RECEIVED", event_id="evt_dup")

    assert response.status_code == 200
    assert response.json() == {"status": "accepted_duplicate"}
    assert confirms == []  # idempotency guard prevented a second stock confirmation
