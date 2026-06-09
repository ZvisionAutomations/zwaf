"""Story-035: bypass deterministico de mensagens de checkout (CPF invalido etc.).

Garante que:
- CPF estruturalmente invalido produz mensagem ACIONAVEL (menciona CPF), distinta
  de campo ausente;
- a tool guarded REGISTRA no result_sink a mensagem deterministica (nao-URL) para
  que o coordenador a envie literal, sem parafrase do LLM.
"""
import asyncio

from zwaf.conversion.checkout_policy import validate_checkout_ready
from zwaf.conversion.payment_gate import (
    _format_missing_checkout_fields,
    make_guarded_payment_link_generator,
)


_ADDR_OK = {
    "postal_code": "01001000", "street": "Praca da Se", "number": "530",
    "district": "Se", "city": "Sao Paulo", "state": "SP",
}
# story-040: o gate agora resolve o endereco via ViaCEP a partir do CEP. Para
# manter este teste deterministico e OFFLINE (sem rede), o endereco usado no
# caminho async omite postal_code E district: sem CEP o resolver nao chama o
# ViaCEP, entao o district permanece faltante (assercao "bairro") e o CPF
# invalido continua sendo reportado (comportamento 035 intacto).
_ADDR_NO_DISTRICT = {k: v for k, v in _ADDR_OK.items() if k not in ("district", "postal_code")}


def test_invalid_cpf_is_distinguished_from_missing():
    v = validate_checkout_ready(
        tenant_id="livia-raiz-vital", product_id="new-woman",
        customer_name="Nilian Oliveira Mendes", customer_document="33143853123",
        delivery_address=_ADDR_OK,
    )
    assert not v.ok
    assert "customer_document_invalid" in v.missing_fields
    assert "customer_document" not in v.missing_fields


def test_missing_cpf_is_not_flagged_as_invalid():
    v = validate_checkout_ready(
        tenant_id="livia-raiz-vital", product_id="new-woman",
        customer_name="Nilian Oliveira Mendes", customer_document="",
        delivery_address=_ADDR_OK,
    )
    assert not v.ok
    assert "customer_document" in v.missing_fields
    assert "customer_document_invalid" not in v.missing_fields


def test_invalid_cpf_message_is_actionable():
    msg = _format_missing_checkout_fields(["customer_document_invalid"])
    assert "CPF" in msg
    assert "valido" in msg  # orienta a corrigir


def test_sink_records_deterministic_reply_on_invalid_cpf():
    sink: dict = {}
    gen = make_guarded_payment_link_generator(
        "livia-raiz-vital",
        {"products": {"new-woman": {"product_id": "nw-001",
         "unit_price_tiers_pix_cents": [{"min_qty": 1, "max_qty": 1, "unit_cents": 14900}]}}},
        result_sink=sink,
    )
    reply = asyncio.run(gen(
        product_id="new-woman", customer_phone="5511999990000",
        customer_name="Nilian Oliveira Mendes", customer_document="33143853123",
        delivery_address=_ADDR_NO_DISTRICT, buying_intent_evidence="quero pagar agora",
    ))
    # mensagem deterministica registrada e NAO e uma URL
    assert sink.get("deterministic_reply") == reply
    assert not reply.startswith("http")
    assert "CPF" in reply          # diz que o problema e o CPF
    assert "bairro" in reply       # e que falta o bairro


if __name__ == "__main__":
    import sys
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except Exception as e:
                failures += 1
                print(f"[FAIL] {name}: {e}")
    sys.exit(1 if failures else 0)
