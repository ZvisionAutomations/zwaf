"""Asaas payment tool unit tests."""
from __future__ import annotations

import httpx
import pytest

from zwaf.tools import payment


PRODUCTS = {
    "new-woman-1": {
        "qty": 1,
        "price_cents_pix": 16590,
        "price_cents_card": 18500,
        "product_id": "nw-001",
    },
    "new-woman-2": {
        "qty": 2,
        "price_cents_pix": 33590,
        "price_cents_card": 34790,
        "product_id": "nw-002",
    },
}


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
                "Asaas error",
                request=httpx.Request("POST", "https://api-sandbox.asaas.com/v3/payments"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/customers"):
            return FakeResponse({"id": "cus_123"})
        if url.endswith("/payments"):
            return FakeResponse({"id": "pay_123", "invoiceUrl": "https://asaas.test/i/pay_123"})
        raise AssertionError(f"unexpected URL {url}")


@pytest.mark.asyncio
async def test_generate_payment_link_creates_customer_and_pix_payment(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.setenv("ASAAS_USER_AGENT", "zwaf-test")
    monkeypatch.setenv("ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ", "19540550000121")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria",
        billing_type="PIX",
    )

    assert result == "https://asaas.test/i/pay_123"
    assert len(fake_client.calls) == 2
    customer_call, payment_call = fake_client.calls
    assert customer_call["url"].endswith("/customers")
    assert customer_call["headers"]["access_token"] == "test-asaas-key"
    assert customer_call["headers"]["User-Agent"] == "zwaf-test"
    assert customer_call["json"]["name"] == "Maria"
    assert customer_call["json"]["mobilePhone"] == "5511999990001"
    assert customer_call["json"]["cpfCnpj"] == "19540550000121"
    assert payment_call["url"].endswith("/payments")
    assert payment_call["json"]["customer"] == "cus_123"
    assert payment_call["json"]["billingType"] == "PIX"
    assert payment_call["json"]["value"] == 165.9
    assert payment_call["json"]["externalReference"] == (
        "livia-raiz-vital:5511999990001:new-woman-1:nw-001"
    )


@pytest.mark.asyncio
async def test_generate_payment_link_uses_package_price_without_multiplying_qty(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link("new-woman-2", "5511999990001")

    assert result == "https://asaas.test/i/pay_123"
    payment_call = fake_client.calls[1]
    assert payment_call["json"]["value"] == 335.9


@pytest.mark.asyncio
async def test_generate_payment_link_requires_asaas_config(monkeypatch):
    monkeypatch.delenv("ASAAS_API_KEY", raising=False)
    monkeypatch.delenv("ASAAS_BASE_URL", raising=False)

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link("new-woman-1", "5511999990001")

    assert result == "Erro ao gerar link: configuracao de pagamento incompleta."


@pytest.mark.asyncio
async def test_generate_payment_link_blocks_product_outside_tenant(monkeypatch):
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link("alpha-pulse-1", "5511999990001")

    assert result == "Erro ao gerar link: produto nao configurado."


@pytest.mark.asyncio
async def test_generate_payment_link_returns_safe_error_on_asaas_http_error(monkeypatch):
    class ErrorClient(FakeAsyncClient):
        async def post(self, url: str, headers: dict, json: dict):
            self.calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse({"errors": [{"description": "invalid"}]}, status_code=401)

    fake_client = ErrorClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link("new-woman-1", "5511999990001")

    assert result == "Erro ao gerar link de pagamento. Por favor, tente novamente."
