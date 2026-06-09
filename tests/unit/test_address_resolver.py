"""Story-040: parser deterministico de endereco + merge/fallback.

Sem rede real — o ViaCEP e sempre injetado/mockado. CEP publico de exemplo
(01001-000 = Praca da Se / Se / Sao Paulo / SP) e numeros ficticios. Sem PII real.
Fixtures usam apenas o endereco publico canonico do ViaCEP — nenhum dado de cliente.
"""
from __future__ import annotations

import asyncio

import pytest

from zwaf.conversion.address_resolver import (
    parse_free_text_address,
    resolve_delivery_address,
)
from zwaf.conversion.checkout_policy import (
    REQUIRED_ADDRESS_FIELDS,
    normalize_delivery_address,
    validate_checkout_ready,
)

# CPF de teste valido (DV correto), nao corresponde a pessoa real.
_VALID_TEST_CPF = "529.982.247-25"

# Endereco em texto livre (CEP publico de exemplo do ViaCEP: 01001-000).
# Exercita o formato: texto livre com numero + complemento e bairro colado na cidade.
_FREE_TEXT_ADDRESS = "CEP 01001-000, Praca da Se, 930 casa 5, Se, Sao Paulo, SP"

# Resposta de ViaCEP mockada (CEP publico de exemplo 01001-000).
_VIACEP_OK = {
    "street": "Praca da Se",
    "district": "Se",
    "city": "Sao Paulo",
    "state": "SP",
}


async def _viacep_ok(cep, *, timeout=3.0):
    return dict(_VIACEP_OK)


async def _viacep_none(cep, *, timeout=3.0):
    return None


# ---------------------------------------------------------------- parser ----

def test_parse_extracts_cep_normalized_8_digits():
    parsed = parse_free_text_address(_FREE_TEXT_ADDRESS)
    assert parsed["postal_code"] == "01001000"


def test_parse_extracts_number_and_complement_casa():
    parsed = parse_free_text_address(_FREE_TEXT_ADDRESS)
    assert parsed["number"] == "930"
    assert "casa" in parsed["complement"].lower()
    assert "5" in parsed["complement"]


@pytest.mark.parametrize(
    "text,expected_number",
    [
        ("930 casa 5", "930"),
        ("n 930", "930"),
        ("nº 930", "930"),
        ("numero 930", "930"),
        ("930/5", "930"),
        ("930 - casa 5", "930"),
        ("Rua X, 100", "100"),
    ],
)
def test_parse_number_variants(text, expected_number):
    assert parse_free_text_address(text)["number"] == expected_number


def test_parse_number_slash_complement():
    parsed = parse_free_text_address("08540-110, 930/5")
    assert parsed["number"] == "930"
    assert parsed["complement"] == "5"
    assert parsed["postal_code"] == "08540110"


def test_cep_not_confused_with_number():
    # O CEP nao pode ser capturado como numero do endereco.
    parsed = parse_free_text_address("01001-000, numero 42")
    assert parsed["postal_code"] == "01001000"
    assert parsed["number"] == "42"


# ----------------------------------------------- normalize string (FR-1) ----

def test_normalize_string_does_not_return_empty_dict():
    # FR-1 / AC-2: string nunca vira {} silencioso.
    result = normalize_delivery_address(_FREE_TEXT_ADDRESS)
    assert result != {}
    assert result["postal_code"] == "01001000"
    assert result["number"] == "930"


def test_normalize_dict_still_works():
    result = normalize_delivery_address(
        {"postal_code": "01001-000", "state": "sp", "number": "930"}
    )
    assert result["postal_code"] == "01001000"
    assert result["state"] == "SP"


# ----------------------------------------------------- resolve + merge ----

def test_resolve_string_with_viacep_completes_all_fields():
    # AC-1: texto livre -> CEP+numero extraidos, ViaCEP completa o resto.
    resolved = asyncio.run(
        resolve_delivery_address(_FREE_TEXT_ADDRESS, viacep_resolver=_viacep_ok)
    )
    assert resolved["postal_code"] == "01001000"
    assert resolved["number"] == "930"
    assert resolved["street"] == "Praca da Se"
    assert resolved["district"] == "Se"
    assert resolved["city"] == "Sao Paulo"
    assert resolved["state"] == "SP"
    # complemento preservado, nao bloqueia
    assert "5" in resolved.get("complement", "")


def test_resolve_cep_plus_number_only_via_viacep():
    # AC-3: cliente fornece apenas CEP + numero; ViaCEP completa rua/bairro/cidade/UF.
    resolved = asyncio.run(
        resolve_delivery_address("01001-000, numero 930", viacep_resolver=_viacep_ok)
    )
    for field_name in REQUIRED_ADDRESS_FIELDS:
        assert resolved.get(field_name, "").strip(), f"{field_name} faltando"


def test_resolve_full_checkout_passes_with_viacep():
    # AC-1 fim a fim: validate_checkout_ready retorna ok com endereco resolvido.
    resolved = asyncio.run(
        resolve_delivery_address(_FREE_TEXT_ADDRESS, viacep_resolver=_viacep_ok)
    )
    v = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman",
        customer_name="Maria Teste Silva",
        customer_document=_VALID_TEST_CPF,
        delivery_address=resolved,
    )
    assert v.ok, v.missing_fields


def test_cep_is_source_of_truth_over_llm_fields():
    # FR-5: ViaCEP prevalece sobre street/district/city/state que o LLM mandou errado.
    llm_dict = {
        "postal_code": "01001-000",
        "number": "930",
        "street": "Rua Errada",
        "district": "Bairro Errado",
        "city": "Cidade Errada",
        "state": "RJ",
    }
    resolved = asyncio.run(
        resolve_delivery_address(llm_dict, viacep_resolver=_viacep_ok)
    )
    assert resolved["street"] == "Praca da Se"
    assert resolved["city"] == "Sao Paulo"
    assert resolved["state"] == "SP"
    assert resolved["number"] == "930"  # numero do cliente preservado


# ----------------------------------------------------------- fallback ----

def test_fallback_viacep_none_uses_llm_fields():
    # AC-4: ViaCEP indisponivel + LLM completo -> usa campos do LLM, link gera.
    llm_dict = {
        "postal_code": "01001-000",
        "number": "930",
        "street": "Praca da Se",
        "district": "Se",
        "city": "Sao Paulo",
        "state": "SP",
    }
    resolved = asyncio.run(
        resolve_delivery_address(llm_dict, viacep_resolver=_viacep_none)
    )
    v = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman",
        customer_name="Maria Teste Silva",
        customer_document=_VALID_TEST_CPF,
        delivery_address=resolved,
    )
    assert v.ok, v.missing_fields


def test_fallback_viacep_none_insufficient_marks_missing():
    # AC-5: ViaCEP fora + dados insuficientes -> nao trava, campos faltantes restam.
    resolved = asyncio.run(
        resolve_delivery_address("01001-000, numero 930", viacep_resolver=_viacep_none)
    )
    v = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman",
        customer_name="Maria Teste Silva",
        customer_document=_VALID_TEST_CPF,
        delivery_address=resolved,
    )
    assert not v.ok
    # so faltam campos de endereco (district/city/state/street), nao CPF/nome
    assert all(f.startswith("delivery_address.") for f in v.missing_fields)


def test_resolve_never_raises_on_garbage():
    # NFR-2: entrada estranha nunca levanta excecao.
    for bad in [None, 123, [], {"x": object()}]:
        try:
            asyncio.run(resolve_delivery_address(bad, viacep_resolver=_viacep_none))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"resolve raised on {bad!r}: {exc}")
