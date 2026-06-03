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

TIERED_PRODUCTS = {
    "new-woman": {
        "product_id": "nw-001",
        "pricing_model": "tiered_unit",
        "card_markup_pct": 10,
        "unit_price_tiers_pix_cents": [
            {"min_qty": 1, "max_qty": 1, "unit_cents": 14900},
            {"min_qty": 2, "max_qty": 4, "unit_cents": 12800},
            {"min_qty": 5, "max_qty": None, "unit_cents": 11990},
        ],
    }
}

VALID_DOCUMENT = "123" + "456" + "789" + "01"
VALID_ADDRESS = {
    "postal_code": "01001000",
    "street": "Rua Teste",
    "number": "100",
    "district": "Centro",
    "city": "Sao Paulo",
    "state": "SP",
}


@pytest.fixture(autouse=True)
def _disable_order_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ZWAF_REQUIRE_ORDER_PERSISTENCE", raising=False)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, method: str = "POST"):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.method = method

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "Asaas error",
                request=httpx.Request(self.method, "https://api-sandbox.asaas.com/v3/payments"),
                response=httpx.Response(self.status_code, text=self.text),
            )


class FakeAsyncClient:
    def __init__(self, existing_customer: dict | None = None, *args, **kwargs):
        self.calls: list[dict] = []
        self.existing_customer = existing_customer

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url: str, headers: dict, params: dict):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        if url.endswith("/customers"):
            data = [self.existing_customer] if self.existing_customer else []
            return FakeResponse({"data": data}, method="GET")
        raise AssertionError(f"unexpected URL {url}")

    async def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        if url.endswith("/customers"):
            return FakeResponse({"id": "cus_123"})
        if url.endswith("/payments"):
            return FakeResponse({"id": "pay_123", "invoiceUrl": "https://asaas.test/i/pay_123"})
        raise AssertionError(f"unexpected URL {url}")

    async def put(self, url: str, headers: dict, json: dict):
        self.calls.append({"method": "PUT", "url": url, "headers": headers, "json": json})
        if "/customers/" in url:
            return FakeResponse({"id": url.rsplit("/", 1)[-1]})
        raise AssertionError(f"unexpected URL {url}")


@pytest.mark.asyncio
async def test_generate_payment_link_creates_customer_and_pix_payment(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.setenv("ASAAS_USER_AGENT", "zwaf-test")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        billing_type="PIX",
    )

    assert result == "https://asaas.test/i/pay_123"
    assert len(fake_client.calls) == 3
    lookup_call, customer_call, payment_call = fake_client.calls
    assert lookup_call["method"] == "GET"
    assert lookup_call["url"].endswith("/customers")
    assert lookup_call["params"] == {
        "externalReference": "livia-raiz-vital:5511999990001",
        "limit": 1,
    }
    assert customer_call["method"] == "POST"
    assert customer_call["url"].endswith("/customers")
    assert customer_call["headers"]["access_token"] == "test-asaas-key"
    assert customer_call["headers"]["User-Agent"] == "zwaf-test"
    assert customer_call["json"]["name"] == "Maria Silva"
    assert customer_call["json"]["mobilePhone"] == "5511999990001"
    assert customer_call["json"]["cpfCnpj"] == VALID_DOCUMENT
    assert payment_call["url"].endswith("/payments")
    assert payment_call["json"]["customer"] == "cus_123"
    assert payment_call["json"]["billingType"] == "PIX"
    assert payment_call["json"]["value"] == 165.9
    assert payment_call["json"]["externalReference"] == (
        "livia-raiz-vital:5511999990001:new-woman-1:nw-001"
    )


@pytest.mark.asyncio
async def test_generate_payment_link_reuses_existing_customer_by_external_reference(monkeypatch):
    fake_client = FakeAsyncClient(
        existing_customer={
            "id": "cus_existing",
            "externalReference": "livia-raiz-vital:5511999990001",
            "cpfCnpj": VALID_DOCUMENT,
        }
    )
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )

    assert result == "https://asaas.test/i/pay_123"
    assert [call["method"] for call in fake_client.calls] == ["GET", "POST"]
    payment_call = fake_client.calls[1]
    assert payment_call["url"].endswith("/payments")
    assert payment_call["json"]["customer"] == "cus_existing"


@pytest.mark.asyncio
async def test_generate_payment_link_updates_existing_customer_document(monkeypatch):
    fake_client = FakeAsyncClient(
        existing_customer={
            "id": "cus_existing",
            "externalReference": "livia-raiz-vital:5511999990001",
        }
    )
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )

    assert result == "https://asaas.test/i/pay_123"
    assert [call["method"] for call in fake_client.calls] == ["GET", "PUT", "POST"]
    update_call = fake_client.calls[1]
    assert update_call["url"].endswith("/customers/cus_existing")
    assert update_call["json"]["cpfCnpj"] == VALID_DOCUMENT
    payment_call = fake_client.calls[2]
    assert payment_call["json"]["customer"] == "cus_existing"


@pytest.mark.asyncio
async def test_generate_payment_link_requires_document_before_creating_customer(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.delenv("ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ", raising=False)

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link("new-woman-1", "5511999990001")

    assert result == "Erro ao gerar link: dados obrigatorios do pedido incompletos."
    assert fake_client.calls == []


def test_tier_unit_cents_selects_correct_bracket():
    tiers = TIERED_PRODUCTS["new-woman"]["unit_price_tiers_pix_cents"]
    assert payment._tier_unit_cents(tiers, 1) == 14900
    assert payment._tier_unit_cents(tiers, 2) == 12800
    assert payment._tier_unit_cents(tiers, 4) == 12800
    assert payment._tier_unit_cents(tiers, 5) == 11990
    assert payment._tier_unit_cents(tiers, 12) == 11990


def test_tiered_total_cents_pix():
    cfg = TIERED_PRODUCTS["new-woman"]
    assert payment._total_cents(cfg, 1, "PIX") == 14900
    assert payment._total_cents(cfg, 3, "PIX") == 38400
    assert payment._total_cents(cfg, 5, "PIX") == 59950
    assert payment._total_cents(cfg, 10, "PIX") == 119900


def test_tiered_total_cents_card_applies_markup():
    cfg = TIERED_PRODUCTS["new-woman"]
    # 14900 * 1.10 = 16390
    assert payment._total_cents(cfg, 1, "CREDIT_CARD") == 16390
    # (12800 * 1.10 = 14080) * 2 = 28160
    assert payment._total_cents(cfg, 2, "CREDIT_CARD") == 28160


def test_resolve_product_and_qty_tiered_and_legacy():
    cfg, qty = payment._resolve_product_and_qty(TIERED_PRODUCTS, "new-woman", 3)
    assert qty == 3
    assert cfg.get("pricing_model") == "tiered_unit"

    # legacy SKU still derives qty from the numeric suffix
    legacy = {"new-woman-2": {"qty": 2, "price_cents_pix": 33590}}
    cfg2, qty2 = payment._resolve_product_and_qty(legacy, "new-woman-2", 0)
    assert qty2 == 2
    assert cfg2["price_cents_pix"] == 33590


@pytest.mark.asyncio
async def test_generate_payment_link_tiered_quantity_three_pix(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": TIERED_PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        billing_type="PIX",
        quantity=3,
    )

    assert result == "https://asaas.test/i/pay_123"
    payment_call = fake_client.calls[2]
    assert payment_call["json"]["value"] == 384.0


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

    result = await generate_payment_link(
        "new-woman-2",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )

    assert result == "https://asaas.test/i/pay_123"
    payment_call = fake_client.calls[2]
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

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )

    assert result == "Erro ao gerar link de pagamento. Por favor, tente novamente."


@pytest.mark.asyncio
async def test_generate_payment_link_does_not_use_default_document_for_real_order(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.setenv("ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ", "123" + "456" + "780" + "00199")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        delivery_address=VALID_ADDRESS,
    )

    assert result == "Erro ao gerar link: dados obrigatorios do pedido incompletos."
    assert fake_client.calls == []
