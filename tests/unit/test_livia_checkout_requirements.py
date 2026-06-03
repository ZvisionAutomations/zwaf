"""Livia checkout policy requirements."""
from __future__ import annotations

from zwaf.conversion.checkout_policy import (
    is_critical_complaint,
    is_opt_out_message,
    validate_checkout_ready,
)


VALID_DOCUMENT = "123" + "456" + "789" + "01"
VALID_ADDRESS = {
    "postal_code": "01001-000",
    "street": "Rua Teste",
    "number": "100",
    "district": "Centro",
    "city": "Sao Paulo",
    "state": "sp",
}


def test_checkout_requires_structured_address_fields():
    result = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman-1",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address={"city": "Sao Paulo"},
    )

    assert result.ok is False
    assert "delivery_address.postal_code" in result.missing_fields
    assert "delivery_address.street" in result.missing_fields


def test_checkout_accepts_complete_new_woman_order():
    result = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman-1",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )

    assert result.ok is True


def test_opt_out_phrases_are_detected():
    assert is_opt_out_message("nao tenho interesse, pode remover")
    assert is_opt_out_message("por favor nao me chame mais")


def test_critical_complaint_phrases_are_detected():
    assert is_critical_complaint("tive reacao adversa")
    assert is_critical_complaint("quero reembolso")
