"""Payment gate business-rule tests."""
from __future__ import annotations

import pytest

from zwaf.conversion.payment_gate import make_guarded_payment_link_generator


VALID_DOCUMENT = "123" + "456" + "789" + "01"
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

    assert "customer_name" in result
    assert "customer_document" in result
    assert "delivery_address.postal_code" in result


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
