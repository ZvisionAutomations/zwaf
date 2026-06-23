"""Asaas payment tool unit tests."""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from zwaf.memory.inventory_store import ReservationResult
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

VALID_DOCUMENT = "529" + "982" + "247" + "25"
PIX_COPY_PASTE = "00020126PIXCOPIAECOLA520400005303986540514.905802BR"
BOLETO_LINE_CODE = "34191.79001 01043.510047 91020.150008 9 99990000016590"
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

    async def get(self, url: str, headers: dict, params: dict | None = None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        if url.endswith("/pixQrCode"):
            return FakeResponse(
                {"payload": PIX_COPY_PASTE, "encodedImage": "iVBORw0KGgoQR=="}, method="GET"
            )
        if url.endswith("/identificationField"):  # story-069 boleto linha digitavel
            return FakeResponse({"identificationField": BOLETO_LINE_CODE}, method="GET")
        if url.endswith("/customers"):
            data = [self.existing_customer] if self.existing_customer else []
            return FakeResponse({"data": data}, method="GET")
        raise AssertionError(f"unexpected URL {url}")

    async def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        if url.endswith("/customers"):
            return FakeResponse({"id": "cus_123"})
        if url.endswith("/payments"):
            # story-069: boleto responde com bankSlipUrl + dueDate, sem a linha
            # digitavel no corpo (forca o GET /identificationField).
            if (json or {}).get("billingType") == "BOLETO":
                return FakeResponse(
                    {
                        "id": "pay_bol",
                        "bankSlipUrl": "https://asaas.test/b/pay_bol.pdf",
                        "dueDate": json.get("dueDate", ""),
                        "status": "PENDING",
                    }
                )
            return FakeResponse({"id": "pay_123", "invoiceUrl": "https://asaas.test/i/pay_123"})
        if url.endswith("/checkouts"):
            return FakeResponse({"id": "chk_123", "link": "https://asaas.test/c/chk_123"})
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

    assert PIX_COPY_PASTE in result  # copia-e-cola entregue no chat (story-041)
    assert len(fake_client.calls) == 4  # lookup, create customer, create payment, pixQrCode
    lookup_call, customer_call, payment_call, pix_call = fake_client.calls
    assert pix_call["url"].endswith("/payments/pay_123/pixQrCode")
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

    assert PIX_COPY_PASTE in result
    assert [call["method"] for call in fake_client.calls] == ["GET", "POST", "GET"]
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

    assert PIX_COPY_PASTE in result
    assert [call["method"] for call in fake_client.calls] == ["GET", "PUT", "POST", "GET"]
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

    assert PIX_COPY_PASTE in result
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

    assert PIX_COPY_PASTE in result
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


# ---------------------------------------------------------------------------
# Inventory reservation integration (story-034)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_payment_link_reserves_stock_before_asaas(monkeypatch):
    captured: dict = {}

    async def fake_reserve(**kwargs):
        captured.update(kwargs)
        return ReservationResult(ok=True, status="reserved")

    monkeypatch.setattr(payment, "reserve_inventory", fake_reserve)
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

    assert PIX_COPY_PASTE in result
    # Reserved the base product slug for the requested quantity.
    assert captured["product_id"] == "new-woman"
    assert captured["quantity"] == 3
    assert captured["tenant_id"] == "livia-raiz-vital"


@pytest.mark.asyncio
async def test_generate_payment_link_blocks_and_skips_asaas_when_unavailable(monkeypatch):
    async def fake_reserve(**kwargs):
        return ReservationResult(ok=False, status="unavailable")

    monkeypatch.setattr(payment, "reserve_inventory", fake_reserve)
    fake_client = FakeAsyncClient()
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

    assert result == payment._MSG_UNAVAILABLE
    assert fake_client.calls == []  # Asaas never contacted


@pytest.mark.asyncio
async def test_generate_payment_link_card_returns_link_message_no_pixqrcode(monkeypatch):
    """Cartao entrega link hospedado via /checkouts sem customerData."""
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.setenv("ASAAS_RETURN_URL", "https://raizvitaloficial.com.br/pagamento")
    monkeypatch.setenv("ASAAS_COMPLETION_URL", "https://raizvitaloficial.com.br/obrigada")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        billing_type="CREDIT_CARD",
    )

    assert "https://asaas.test/c/chk_123" in result
    assert "cartao" in result.lower()
    assert "parcel" in result.lower()
    assert not any(call["url"].endswith("/pixQrCode") for call in fake_client.calls)
    assert not any(call["url"].endswith("/customers") for call in fake_client.calls)
    checkout_call = next(
        c for c in fake_client.calls if c["method"] == "POST" and c["url"].endswith("/checkouts")
    )
    assert checkout_call["json"]["billingTypes"] == ["CREDIT_CARD"]
    assert checkout_call["json"]["chargeTypes"] == ["DETACHED"]
    assert "customerData" not in checkout_call["json"]
    assert "customer" not in checkout_call["json"]


@pytest.mark.asyncio
async def test_generate_payment_link_card_checkout_uses_configured_callback(monkeypatch):
    """Hosted checkout sends required callback URLs without PII."""
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.setenv("ASAAS_RETURN_URL", "https://raizvitaloficial.com.br/pagamento")
    monkeypatch.setenv("ASAAS_COMPLETION_URL", "https://raizvitaloficial.com.br/obrigada")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS},
    )

    await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        billing_type="CREDIT_CARD",
    )

    checkout_call = next(
        c for c in fake_client.calls if c["method"] == "POST" and c["url"].endswith("/checkouts")
    )
    callback = checkout_call["json"]["callback"]
    success_url = callback["successUrl"]
    assert success_url == "https://raizvitaloficial.com.br/obrigada"
    assert callback["cancelUrl"] == "https://raizvitaloficial.com.br/pagamento"
    # Sem PII na URL (AC-6).
    assert VALID_DOCUMENT not in success_url
    assert "5511999990001" not in success_url
    assert "Maria" not in success_url


@pytest.mark.asyncio
async def test_card_checkout_requires_callback_urls(monkeypatch):
    """Asaas Checkout requires callback URLs; without them we fail before API call."""
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")
    monkeypatch.delenv("ASAAS_RETURN_URL", raising=False)
    monkeypatch.delenv("ASAAS_COMPLETION_URL", raising=False)

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital", {"products": PRODUCTS}
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        billing_type="CREDIT_CARD",
    )

    assert result == payment._MSG_GENERIC_ERROR
    assert fake_client.calls == []


def test_card_callback_skipped_without_flag(monkeypatch):
    """Sem a flag card_callback_enabled, nenhum callback (mesmo com return_url)."""
    monkeypatch.setenv("ASAAS_RETURN_URL", "https://raizvitaloficial.com.br/obrigada")
    assert payment._build_card_callback({}) is None
    assert payment._build_card_callback({"card_callback_enabled": False}) is None


def test_card_message_embeds_value_with_markup():
    """A mensagem de cartao exibe o valor a vista (com markup) e fala em parcelar."""
    msg = payment._card_message("https://asaas.test/i/pay_123", 16390)
    assert "https://asaas.test/i/pay_123" in msg
    assert "163,90" in msg
    assert "parcel" in msg.lower()


@pytest.mark.asyncio
async def test_generate_payment_link_pix_without_payload_releases_reservation(monkeypatch):
    """Se o Asaas nao devolver o copia-e-cola, libera a reserva e nao promete (story-041)."""
    released: list = []

    async def fake_order_draft(**kwargs):
        return "order-xyz"

    async def fake_reserve(**kwargs):
        return ReservationResult(ok=True, status="reserved")

    async def fake_release(**kwargs):
        released.append(kwargs["order_id"])
        return True

    async def fake_mark_failed(**kwargs):
        pass

    monkeypatch.setattr(payment, "create_order_draft", fake_order_draft)
    monkeypatch.setattr(payment, "reserve_inventory", fake_reserve)
    monkeypatch.setattr(payment, "release_reservation", fake_release)
    monkeypatch.setattr(payment, "mark_order_payment_failed", fake_mark_failed)

    class NoPayloadClient(FakeAsyncClient):
        async def get(self, url: str, headers: dict, params: dict | None = None):
            self.calls.append({"method": "GET", "url": url})
            if url.endswith("/pixQrCode"):
                return FakeResponse({"payload": ""}, method="GET")
            return FakeResponse({"data": []}, method="GET")

    fake_client = NoPayloadClient()
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

    assert result == payment._MSG_GENERIC_ERROR
    assert released == ["order-xyz"]


@pytest.mark.asyncio
async def test_generate_payment_link_releases_reservation_on_asaas_error(monkeypatch):
    released: list = []
    marked_failed: list = []

    async def fake_order_draft(**kwargs):
        return "order-123"

    async def fake_reserve(**kwargs):
        return ReservationResult(ok=True, status="reserved")

    async def fake_release(**kwargs):
        released.append(kwargs["order_id"])
        return True

    async def fake_mark_failed(**kwargs):
        marked_failed.append(kwargs["order_id"])

    monkeypatch.setattr(payment, "create_order_draft", fake_order_draft)
    monkeypatch.setattr(payment, "reserve_inventory", fake_reserve)
    monkeypatch.setattr(payment, "release_reservation", fake_release)
    monkeypatch.setattr(payment, "mark_order_payment_failed", fake_mark_failed)

    class ErrorClient(FakeAsyncClient):
        async def post(self, url: str, headers: dict, json: dict):
            self.calls.append({"url": url, "headers": headers, "json": json})
            if url.endswith("/customers"):
                return FakeResponse({"id": "cus_123"})
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

    assert result == payment._MSG_GENERIC_ERROR
    assert released == ["order-123"]  # reservation freed
    assert marked_failed == ["order-123"]  # order flagged payment_link_failed


# ---------------------------------------------------------------------------
# story-069: boleto (vence em 24h) como 3a forma de pagamento
# ---------------------------------------------------------------------------


def test_resolve_due_days_boleto_is_d_plus_1():
    assert payment._resolve_due_days({}, "BOLETO") == 1  # default D+1 (24h)
    assert payment._resolve_due_days({"boleto_due_days": 3}, "BOLETO") == 3
    assert payment._resolve_due_days({"boleto_due_days": -5}, "BOLETO") == 0  # nunca < hoje
    assert payment._resolve_due_days({}, "PIX") == 2  # demais meios mantem default


def test_format_due_date_br():
    assert payment._format_due_date_br("2026-06-23") == "23/06/2026"
    assert payment._format_due_date_br("invalid") == ""
    assert payment._format_due_date_br("") == ""


def test_boleto_message_with_line_code_splits_and_has_pdf_and_due():
    msg = payment._boleto_message(
        BOLETO_LINE_CODE, "https://asaas.test/b/x.pdf", "2026-06-23", 16590
    )
    parts = msg.split(payment.MESSAGE_SPLIT)
    assert len(parts) == 2
    assert parts[1] == BOLETO_LINE_CODE  # linha digitavel PURA na 2a mensagem
    assert "https://asaas.test/b/x.pdf" in parts[0]
    assert "23/06/2026" in parts[0]
    assert "R$ 165,90" in parts[0]
    assert "antecedencia" in parts[0]  # aviso de prazo/compensacao


def test_boleto_message_without_line_code_falls_back_to_pdf_only():
    msg = payment._boleto_message("", "https://asaas.test/b/x.pdf", "2026-06-23", 16590)
    assert payment.MESSAGE_SPLIT not in msg
    assert "https://asaas.test/b/x.pdf" in msg
    assert "23/06/2026" in msg


@pytest.mark.asyncio
async def test_generate_payment_link_boleto_d_plus_1_with_line_and_pdf(monkeypatch):
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    generate_payment_link = payment.make_payment_link_generator(
        "livia-raiz-vital",
        {"products": PRODUCTS, "boleto_due_days": 1},
    )

    result = await generate_payment_link(
        "new-woman-1",
        "5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        billing_type="BOLETO",
    )

    # AC-2: linha digitavel + PDF na resposta
    assert BOLETO_LINE_CODE in result
    assert "https://asaas.test/b/pay_bol.pdf" in result

    # AC-1/AC-4: cobranca BOLETO com dueDate = hoje + 1 dia (nunca < hoje)
    payment_call = next(c for c in fake_client.calls if c["url"].endswith("/payments"))
    assert payment_call["json"]["billingType"] == "BOLETO"
    expected_due = (date.today() + timedelta(days=1)).isoformat()
    assert payment_call["json"]["dueDate"] == expected_due
    assert payment_call["json"]["dueDate"] >= date.today().isoformat()

    # buscou a linha digitavel via GET /identificationField (story-069)
    assert any(c["url"].endswith("/identificationField") for c in fake_client.calls)
    # preco do boleto = preco Pix (sem markup): 165.90
    assert payment_call["json"]["value"] == 165.9


@pytest.mark.asyncio
async def test_generate_payment_link_boleto_reserves_stock_before_charge(monkeypatch):
    """AC-5: estoque reservado ANTES de criar a cobranca; sem reserva, sem boleto."""
    fake_client = FakeAsyncClient()
    monkeypatch.setattr(payment.httpx, "AsyncClient", lambda **kwargs: fake_client)
    monkeypatch.setenv("ASAAS_API_KEY", "test-asaas-key")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://api-sandbox.asaas.com/v3")

    reserve_calls: list[dict] = []

    async def fake_reserve(*, tenant_id, product_id, quantity, order_id):
        reserve_calls.append({"product_id": product_id, "quantity": quantity})
        return ReservationResult(ok=True, status="reserved")

    monkeypatch.setattr(payment, "reserve_inventory", fake_reserve)

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
        billing_type="BOLETO",
    )

    assert BOLETO_LINE_CODE in result
    assert reserve_calls and reserve_calls[0]["quantity"] == 1  # reservou antes da cobranca
