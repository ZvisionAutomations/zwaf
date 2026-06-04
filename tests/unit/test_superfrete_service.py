"""SuperFrete shipping service unit tests."""
from __future__ import annotations

import pytest

from zwaf.shipping import service


class FakeSuperFreteClient:
    def __init__(self, *, checkout_has_print: bool = True):
        self.add_to_cart_calls = 0
        self.checkout_calls = 0
        self.tag_print_calls = 0
        self.checkout_has_print = checkout_has_print

    async def add_to_cart(self, **kwargs):
        self.add_to_cart_calls += 1
        return {"id": "sf_order_123", "status": "created"}

    async def checkout(self, order_ids):
        self.checkout_calls += 1
        assert order_ids == ["sf_order_123"]
        order = {
            "id": "sf_order_123",
            "service_id": 1,
            "tracking": "DG048745602BR",
        }
        if self.checkout_has_print:
            order["print"] = {"url": "https://sandbox.superfrete.com/label.pdf"}
        return {
            "success": True,
            "purchase": {
                "status": "paid",
                "orders": [order],
            },
        }

    async def tag_print(self, order_ids):
        self.tag_print_calls += 1
        assert order_ids == ["sf_order_123"]
        return {"url": "https://sandbox.superfrete.com/fallback-label.pdf"}


@pytest.mark.asyncio
async def test_create_label_for_order_persists_cart_and_checkout(monkeypatch):
    upserts: list[dict] = []

    async def fake_context(order_id: str):
        assert order_id == "order-123"
        return {
            "order_id": "order-123",
            "tenant_id": "livia-raiz-vital",
            "product_id": "new-woman",
            "quantity": 2,
            "total_cents": 25600,
            "customer_name": "Cliente Teste",
            "customer_document": "doc-redacted",
            "postal_code": "20020050",
            "street": "Rua Teste",
            "number": "10",
            "district": "Centro",
            "city": "Rio de Janeiro",
            "state": "RJ",
        }

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)
        return "shipment-123"

    monkeypatch.setattr(service.order_store, "get_order_shipping_context", fake_context)
    monkeypatch.setattr(service.order_store, "get_superfrete_shipment_for_order", _missing_shipment)
    monkeypatch.setattr(service.order_store, "upsert_shipment", fake_upsert)
    monkeypatch.setenv("SUPERFRETE_AUTO_CHECKOUT_ENABLED", "true")

    client = FakeSuperFreteClient()
    result = await service.create_label_for_order(
        order_id="order-123",
        service_id=1,
        client=client,
    )

    assert result["status"] == "label_created"
    assert result["tracking_code"] == "DG048745602BR"
    assert result["label_url"].endswith("label.pdf")
    assert client.add_to_cart_calls == 1
    assert client.checkout_calls == 1
    assert client.tag_print_calls == 0
    assert [call["event_type"] for call in upserts] == ["label_cart_created", "label_checkout"]
    assert upserts[0]["provider"] == "superfrete"
    assert upserts[1]["tracking_code"] == "DG048745602BR"


@pytest.mark.asyncio
async def test_create_label_for_order_falls_back_to_tag_print(monkeypatch):
    async def fake_context(order_id: str):
        return {
            "order_id": order_id,
            "product_id": "new-woman",
            "quantity": 1,
            "total_cents": 14900,
            "customer_name": "Cliente Teste",
            "postal_code": "20020050",
            "street": "Rua Teste",
            "number": "10",
            "district": "Centro",
            "city": "Rio de Janeiro",
            "state": "RJ",
        }

    async def fake_upsert(**kwargs):
        return "shipment-123"

    monkeypatch.setattr(service.order_store, "get_order_shipping_context", fake_context)
    monkeypatch.setattr(service.order_store, "get_superfrete_shipment_for_order", _missing_shipment)
    monkeypatch.setattr(service.order_store, "upsert_shipment", fake_upsert)
    monkeypatch.setenv("SUPERFRETE_AUTO_CHECKOUT_ENABLED", "true")
    client = FakeSuperFreteClient(checkout_has_print=False)

    result = await service.create_label_for_order(order_id="order-123", service_id=1, client=client)

    assert result["status"] == "label_created"
    assert result["label_url"].endswith("fallback-label.pdf")
    assert client.tag_print_calls == 1


@pytest.mark.asyncio
async def test_create_label_for_order_is_idempotent_when_label_exists(monkeypatch):
    async def fake_context(order_id: str):
        return {"order_id": order_id, "quantity": 1}

    async def existing_shipment(order_id: str):
        return {
            "external_shipment_id": "sf_order_123",
            "tracking_code": "DG048745602BR",
            "status": "generated",
        }

    monkeypatch.setattr(service.order_store, "get_order_shipping_context", fake_context)
    monkeypatch.setattr(service.order_store, "get_superfrete_shipment_for_order", existing_shipment)
    client = FakeSuperFreteClient()

    result = await service.create_label_for_order(order_id="order-123", service_id=1, client=client)

    assert result["status"] == "label_exists"
    assert result["provider_order_id"] == "sf_order_123"
    assert client.add_to_cart_calls == 0
    assert client.checkout_calls == 0


@pytest.mark.asyncio
async def test_create_label_for_order_reuses_existing_cart_without_new_cart(monkeypatch):
    upserts: list[dict] = []

    async def fake_context(order_id: str):
        return {"order_id": order_id, "quantity": 1}

    async def existing_shipment(order_id: str):
        return {
            "external_shipment_id": "sf_order_123",
            "tracking_code": "",
            "status": "created",
        }

    async def fake_upsert(**kwargs):
        upserts.append(kwargs)
        return "shipment-123"

    monkeypatch.setattr(service.order_store, "get_order_shipping_context", fake_context)
    monkeypatch.setattr(service.order_store, "get_superfrete_shipment_for_order", existing_shipment)
    monkeypatch.setattr(service.order_store, "upsert_shipment", fake_upsert)
    monkeypatch.setenv("SUPERFRETE_AUTO_CHECKOUT_ENABLED", "true")
    client = FakeSuperFreteClient()

    result = await service.create_label_for_order(order_id="order-123", service_id=1, client=client)

    assert result["status"] == "label_created"
    assert client.add_to_cart_calls == 0
    assert client.checkout_calls == 1
    assert upserts[0]["event_type"] == "label_checkout"


@pytest.mark.asyncio
async def test_create_label_for_order_defaults_to_manual_fulfillment(monkeypatch):
    async def fake_context(order_id: str):
        return {
            "order_id": order_id,
            "product_id": "new-woman",
            "quantity": 1,
            "total_cents": 14900,
            "customer_name": "Cliente Teste",
            "postal_code": "20020050",
            "street": "Rua Teste",
            "number": "10",
            "district": "Centro",
            "city": "Rio de Janeiro",
            "state": "RJ",
        }

    async def fake_upsert(**kwargs):
        return "shipment-123"

    monkeypatch.setattr(service.order_store, "get_order_shipping_context", fake_context)
    monkeypatch.setattr(service.order_store, "get_superfrete_shipment_for_order", _missing_shipment)
    monkeypatch.setattr(service.order_store, "upsert_shipment", fake_upsert)
    monkeypatch.delenv("SUPERFRETE_AUTO_CHECKOUT_ENABLED", raising=False)
    client = FakeSuperFreteClient()

    result = await service.create_label_for_order(order_id="order-123", service_id=1, client=client)

    assert result["status"] == "manual_fulfillment_pending"
    assert result["provider_order_id"] == "sf_order_123"
    assert client.add_to_cart_calls == 1
    assert client.checkout_calls == 0


async def _missing_shipment(order_id: str):
    return {}
