"""SuperFrete shipping service unit tests."""
from __future__ import annotations

import pytest

from zwaf.shipping import service


class FakeSuperFreteClient:
    async def add_to_cart(self, **kwargs):
        return {"id": "sf_order_123", "status": "created"}

    async def checkout(self, order_ids):
        assert order_ids == ["sf_order_123"]
        return {
            "success": True,
            "purchase": {
                "status": "paid",
                "orders": [
                    {
                        "id": "sf_order_123",
                        "service_id": 1,
                        "tracking": "DG048745602BR",
                        "print": {"url": "https://sandbox.superfrete.com/label.pdf"},
                    }
                ],
            },
        }


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
    monkeypatch.setattr(service.order_store, "upsert_shipment", fake_upsert)

    result = await service.create_label_for_order(
        order_id="order-123",
        service_id=1,
        client=FakeSuperFreteClient(),
    )

    assert result["status"] == "label_created"
    assert result["tracking_code"] == "DG048745602BR"
    assert result["label_url"].endswith("label.pdf")
    assert [call["event_type"] for call in upserts] == ["label_cart_created", "label_checkout"]
    assert upserts[0]["provider"] == "superfrete"
    assert upserts[1]["tracking_code"] == "DG048745602BR"
