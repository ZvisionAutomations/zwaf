"""SuperFrete client unit tests."""
from __future__ import annotations

import httpx
import pytest

from zwaf.shipping.superfrete import (
    SuperFreteClient,
    SuperFreteConfig,
    normalize_quote_services,
    package_from_env,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "SuperFrete error",
                request=httpx.Request("POST", "https://sandbox.superfrete.com/api/v0/calculator"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class FakeAsyncClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/calculator"):
            return FakeResponse(
                {
                    "services": [
                        {"id": 1, "name": "PAC", "price": 25.5, "delivery_time": 5},
                    ],
                    "package": {"height": 6, "width": 16, "length": 24, "weight": 0.3},
                }
            )
        if url.endswith("/cart"):
            return FakeResponse({"id": "sf_order_123", "status": "created"})
        if url.endswith("/checkout"):
            return FakeResponse(
                {
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
            )
        raise AssertionError(f"unexpected URL {url}")


@pytest.mark.asyncio
async def test_calculate_uses_bearer_user_agent_and_payload():
    fake_client = FakeAsyncClient()
    client = SuperFreteClient(
        SuperFreteConfig(
            token="test-token",
            base_url="https://sandbox.superfrete.com",
            user_agent="ZWAF Test/1.0",
        ),
        client=fake_client,
    )

    result = await client.calculate(
        from_postal_code="01153-000",
        to_postal_code="20020-050",
        services="1,2,17",
        package={"height": 6, "width": 16, "length": 24, "weight": 0.3},
    )

    assert result["services"][0]["service_id"] == 1
    call = fake_client.calls[0]
    assert call["url"] == "https://sandbox.superfrete.com/api/v0/calculator"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert call["headers"]["User-Agent"] == "ZWAF Test/1.0"
    assert call["json"]["from"]["postal_code"] == "01153000"
    assert call["json"]["to"]["postal_code"] == "20020050"
    assert call["json"]["services"] == "1,2,17"


@pytest.mark.asyncio
async def test_cart_and_checkout_return_tracking():
    fake_client = FakeAsyncClient()
    client = SuperFreteClient(
        SuperFreteConfig(token="test-token", user_agent="ZWAF Test/1.0"),
        client=fake_client,
    )

    cart = await client.add_to_cart(
        sender={"name": "Loja Raiz Vital", "postal_code": "01153000"},
        recipient={"name": "Cliente Teste", "postal_code": "20020050"},
        service=1,
        products=[{"name": "New Woman", "quantity": 1, "unitary_value": 149.0}],
        volume={"height": 6, "width": 16, "length": 24, "weight": 0.3},
    )
    checkout = await client.checkout([cart["id"]])

    assert cart["id"] == "sf_order_123"
    order = checkout["purchase"]["orders"][0]
    assert order["tracking"] == "DG048745602BR"
    assert fake_client.calls[1]["json"]["orders"] == ["sf_order_123"]


def test_normalize_quote_services_accepts_list_and_dict_shapes():
    assert normalize_quote_services([{"code": 2, "service_name": "Sedex", "final_price": 40}]) == [
        {
            "service_id": 2,
            "name": "Sedex",
            "price": 40,
            "delivery_time": None,
            "raw": {"code": 2, "service_name": "Sedex", "final_price": 40},
        }
    ]
    assert normalize_quote_services({"services": [{"id": 1, "name": "PAC"}]})[0]["name"] == "PAC"


def test_package_from_env_multiplies_weight_by_quantity(monkeypatch):
    monkeypatch.setenv("SUPERFRETE_PACKAGE_UNIT_WEIGHT_KG", "0.25")
    assert package_from_env(3)["weight"] == 0.75
