"""Story-074: corrigir loop de endereco (parser street/cidade + numero do CPF).

Reproduz o caso real do Fernando (multi-linha com CPF + endereco completo em
texto livre):
- BUG-2: o numero da casa nao pode ser confundido com os digitos do CPF;
- BUG-1: street/district/city/state sao extraidos do texto livre, alimentando o
  fallback de ``advance_checkout`` quando o ViaCEP nao resolve (sem loop).

Sem rede real (ViaCEP mockado). CPF de teste com DV valido; CEP publico de
exemplo. Nenhum dado de cliente real.
"""
from __future__ import annotations

import pytest

from zwaf.conversion import checkout_flow as cf
from zwaf.conversion.address_resolver import parse_free_text_address

# CPF de teste estruturalmente valido (DV correto), sem pessoa real associada.
VALID_CPF = "529.982.247-25"

# Caso real (multi-linha SEM rotulos): nome + CPF + CEP + rua + numero + bairro
# + cidade + UF numa unica mensagem. Numero da casa = 930.
_REAL_CASE = (
    "Fernando Augusto Silva\n"
    f"{VALID_CPF}\n"
    "06754-110\n"
    "Rua das Palmeiras 930\n"
    "Centro\n"
    "Taboao da Serra\n"
    "SP"
)


# --------------------------------------------------------------- BUG-2 ------


def test_free_text_number_is_house_not_cpf():
    """BUG-2: com CPF e endereco juntos, o numero e o da casa (930), nunca os
    6 primeiros digitos do CPF."""
    parsed = parse_free_text_address(_REAL_CASE)
    assert parsed["number"] == "930"


def test_parse_message_number_not_confused_with_cpf():
    parsed = cf.parse_message(_REAL_CASE)
    assert parsed["number"] == "930"
    assert cf.only_digits(parsed["document"]) == cf.only_digits(VALID_CPF)


# --------------------------------------------------------------- BUG-1 ------


def test_address_parts_extracted_from_free_text():
    """AC-1: street/district/city/state vem do texto livre nao-rotulado."""
    parsed = cf.parse_message(_REAL_CASE)
    assert parsed.get("street", "").lower().startswith("rua das palmeiras")
    assert parsed["district"] == "Centro"
    assert parsed["city"] == "Taboao da Serra"
    assert parsed["state"] == "SP"


def test_trailing_uf_on_city_line():
    """Variante: cidade e UF na mesma linha ('Sao Paulo - SP')."""
    text = (
        "Joao Pereira Lima\n"
        f"{VALID_CPF}\n"
        "01001-000\n"
        "Av Paulista 1000\n"
        "Bela Vista\n"
        "Sao Paulo - SP"
    )
    parsed = cf.parse_message(text)
    assert parsed["state"] == "SP"
    assert parsed["city"] == "Sao Paulo"
    assert parsed["district"] == "Bela Vista"


# ----------------------------------------- advance_checkout (sem ViaCEP) ----


@pytest.mark.asyncio
async def test_advance_completes_without_viacep(monkeypatch):
    """AC-2: ViaCEP indisponivel + cliente escreveu rua/bairro/cidade/UF ->
    checkout completa pelo fallback (sem loop)."""

    async def fake_resolve(address, *, timeout=3.0, **kwargs):
        # ViaCEP None -> resolve so devolve o que veio do cliente (CEP/numero).
        return {
            "postal_code": cf.only_digits(address.get("postal_code", "")),
            "number": address.get("number", ""),
        }

    monkeypatch.setattr(cf, "resolve_delivery_address", fake_resolve)
    turn = await cf.advance_checkout(_REAL_CASE)
    assert turn.ready is True
    assert turn.reply == ""
    assert turn.collected["number"] == "930"
    assert turn.resolved_address["city"] == "Taboao da Serra"
    assert turn.resolved_address["state"] == "SP"


@pytest.mark.asyncio
async def test_viacep_is_source_of_truth(monkeypatch):
    """AC-3: quando o ViaCEP responde, street/district/city/state vem do ViaCEP
    (FR-5 preservado), nao do texto livre do cliente."""

    async def fake_resolve(address, *, timeout=3.0, **kwargs):
        return {
            "postal_code": cf.only_digits(address.get("postal_code", "")),
            "number": address.get("number", ""),
            "street": "Rua Oficial ViaCEP",
            "district": "Bairro ViaCEP",
            "city": "Cidade ViaCEP",
            "state": "RJ",
        }

    monkeypatch.setattr(cf, "resolve_delivery_address", fake_resolve)
    turn = await cf.advance_checkout(_REAL_CASE)
    assert turn.ready is True
    assert turn.resolved_address["city"] == "Cidade ViaCEP"
    assert turn.resolved_address["state"] == "RJ"
    assert turn.resolved_address["street"] == "Rua Oficial ViaCEP"


# ------------------------------------------------------- nao-regressao ------


def test_no_regression_number_with_complement_040():
    """AC-4: caso 040 ('930 casa 5', virgula) segue funcionando."""
    parsed = parse_free_text_address("CEP 01001-000, Praca da Se, 930 casa 5")
    assert parsed["number"] == "930"
    assert "casa" in parsed["complement"].lower()
