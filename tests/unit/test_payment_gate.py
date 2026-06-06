"""Payment gate business-rule tests."""
from __future__ import annotations

import pytest

from zwaf.conversion.payment_gate import make_guarded_payment_link_generator


VALID_DOCUMENT = "529" + "982" + "247" + "25"
VALID_ADDRESS = {
    "postal_code": "01001000",
    "street": "Rua Teste",
    "number": "100",
    "district": "Centro",
    "city": "Sao Paulo",
    "state": "SP",
}


@pytest.mark.asyncio
async def test_guard_blocks_link_without_checkout_data(monkeypatch):
    generator = make_guarded_payment_link_generator(
        "livia-raiz-vital",
        {"products": {"new-woman-1": {"qty": 1, "price_cents_pix": 16590}}},
    )

    result = await generator(
        product_id="new-woman-1",
        customer_phone="5511999990001",
        buying_intent_evidence="quero fechar agora",
    )

    assert "nome completo" in result
    assert "CPF/CNPJ valido" in result
    assert "CEP" in result
    assert "rua" in result


@pytest.mark.asyncio
async def test_guard_blocks_alpha_pulse_for_livia():
    generator = make_guarded_payment_link_generator("livia-raiz-vital", {"products": {}})

    result = await generator(
        product_id="alpha-pulse-1",
        customer_phone="5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        buying_intent_evidence="quero fechar agora",
    )

    assert "Alpha Pulse" in result
    assert "Caio" in result


@pytest.mark.asyncio
async def test_guard_blocks_new_woman_for_caio():
    generator = make_guarded_payment_link_generator("caio-alpha-pulse", {"products": {}})

    result = await generator(
        product_id="new-woman-1",
        customer_phone="5511999990002",
        customer_name="Joao Souza",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        buying_intent_evidence="quero fechar agora",
    )

    assert "New Woman" in result
    assert "Livia" in result


@pytest.mark.asyncio
async def test_guard_allows_alpha_pulse_tiered_for_caio():
    generator = make_guarded_payment_link_generator(
        "caio-alpha-pulse",
        {
            "products": {
                "alpha-pulse": {
                    "product_id": "ap-001",
                    "card_markup_pct": 10,
                    "unit_price_tiers_pix_cents": [
                        {"min_qty": 1, "max_qty": 1, "unit_cents": 14900},
                        {"min_qty": 2, "max_qty": 4, "unit_cents": 12800},
                        {"min_qty": 5, "max_qty": None, "unit_cents": 11990},
                    ],
                }
            }
        },
    )

    # missing checkout data -> not a blocked_product, asks for data (no Asaas config)
    result = await generator(
        product_id="alpha-pulse",
        customer_phone="5511999990002",
        buying_intent_evidence="quero fechar agora",
        quantity=3,
    )

    assert "New Woman" not in result
    assert "nome completo" in result


@pytest.mark.asyncio
async def test_guard_allows_short_confirmation_after_checkout_data(monkeypatch):
    calls = []

    def fake_raw_generator(tenant_id, payment_config):
        async def generate_payment_link(**kwargs):
            calls.append({"tenant_id": tenant_id, **kwargs})
            return "https://asaas.example/pay/123"

        return generate_payment_link

    monkeypatch.setattr(
        "zwaf.conversion.payment_gate.make_payment_link_generator",
        fake_raw_generator,
    )
    generator = make_guarded_payment_link_generator(
        "livia-raiz-vital",
        {"products": {"new-woman": {"qty": 1, "price_cents_pix": 14900}}},
    )

    result = await generator(
        product_id="new-woman",
        customer_phone="5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        buying_intent_evidence="sim",
        billing_type="PIX",
        quantity=1,
    )

    assert result == "https://asaas.example/pay/123"
    assert calls[0]["tenant_id"] == "livia-raiz-vital"
    assert calls[0]["billing_type"] == "PIX"


@pytest.mark.asyncio
async def test_guard_allows_generate_link_request_after_checkout_data(monkeypatch):
    calls = []

    def fake_raw_generator(tenant_id, payment_config):
        async def generate_payment_link(**kwargs):
            calls.append(kwargs)
            return "https://asaas.example/pay/456"

        return generate_payment_link

    monkeypatch.setattr(
        "zwaf.conversion.payment_gate.make_payment_link_generator",
        fake_raw_generator,
    )
    generator = make_guarded_payment_link_generator(
        "livia-raiz-vital",
        {"products": {"new-woman": {"qty": 1, "price_cents_pix": 14900}}},
    )

    result = await generator(
        product_id="new-woman",
        customer_phone="5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        buying_intent_evidence="pode gerar o link de pagamento",
        billing_type="PIX",
        quantity=1,
    )

    assert result == "https://asaas.example/pay/456"
    assert calls[0]["billing_type"] == "PIX"
    assert calls[0]["quantity"] == 1


@pytest.mark.asyncio
async def test_guard_does_not_loop_on_weak_followup_after_checkout_complete(monkeypatch):
    calls = []

    def fake_raw_generator(tenant_id, payment_config):
        async def generate_payment_link(**kwargs):
            calls.append(kwargs)
            return "https://asaas.example/pay/789"

        return generate_payment_link

    monkeypatch.setattr(
        "zwaf.conversion.payment_gate.make_payment_link_generator",
        fake_raw_generator,
    )
    generator = make_guarded_payment_link_generator(
        "livia-raiz-vital",
        {"products": {"new-woman": {"qty": 1, "price_cents_pix": 14900}}},
    )

    result = await generator(
        product_id="new-woman",
        customer_phone="5511999990001",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
        buying_intent_evidence="",
        billing_type="PIX",
        quantity=1,
    )

    assert result == "https://asaas.example/pay/789"
    assert len(calls) == 1
