"""Testes da coleta deterministica de checkout (story-041)."""
from __future__ import annotations

import pytest

from zwaf.conversion import checkout_flow as cf

VALID_CPF = "529" + "982" + "247" + "25"  # CPF estruturalmente valido


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_labeled_extracts_all_fields():
    text = "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930"
    parsed = cf.parse_labeled(text)
    assert parsed["name"] == "Maria Silva"
    assert parsed["document"] == "529.982.247-25"
    assert parsed["postal_code"] == "01001-000"
    assert parsed["number"] == "930"


def test_parse_message_free_text_fallback():
    # Sem rotulos: extrai CEP/numero (story-040) e CPF (11 digitos).
    text = "meu cep eh 01001000 numero 930, cpf 529.982.247-25"
    parsed = cf.parse_message(text)
    assert parsed["postal_code"] == "01001000"
    assert parsed["number"] == "930"
    assert cf.only_digits(parsed["document"]) == VALID_CPF


def test_parse_message_labeled_takes_precedence_over_free():
    text = "CEP: 01001-000\nNumero: 930 casa 5"
    parsed = cf.parse_message(text)
    assert parsed["postal_code"] == "01001-000"
    assert parsed["number"] == "930"


# ---------------------------------------------------------------------------
# story-063: numero embutido na linha de endereco em mensagem rotulada
# ---------------------------------------------------------------------------


def test_parse_message_recovers_number_from_unlabeled_address_line():
    """AC-3: rotulos + endereco solto com numero embutido (dados sinteticos).

    Antes da story-063 o parser confiava so nos rotulos; a linha "Rua ... 52,
    casa 97" (sem rotulo Numero:) era ignorada e o checkout pedia "faltou numero".
    """
    text = (
        "Nome: Mariana Costa Lima\n"
        f"Cpf: {VALID_CPF}\n"
        "CEP: 01001000\n"
        "Rua das Acacias 52, casa 97, Sao Paulo"
    )
    parsed = cf.parse_message(text)
    assert parsed["number"] == "52"
    assert "casa 97" in parsed.get("complement", "")
    # E o numero NAO e mais pedido.
    assert "number" not in cf.pending_required(cf.merge_collected({}, parsed))


def test_parse_message_number_recovery_ignores_cpf_cep_digits():
    """AC-4/FR-5: digitos de CPF (11) e CEP (8) nunca viram numero da casa."""
    text = (
        "Nome: Maria Silva\n"
        "CPF: 529.982.247-25\n"
        "CEP: 01001-000\n"
        "Rua das Flores, Centro, Sao Paulo"  # endereco SEM numero
    )
    parsed = cf.parse_message(text)
    assert not parsed.get("number")
    assert "number" in cf.pending_required(cf.merge_collected({}, parsed))


def test_number_recovery_skips_line_carrying_cpf_digits():
    """FR-5: uma linha sem rotulo que contem o CPF nao vira numero da casa."""
    text = (
        "CEP: 01001-000\n"
        "Documento 529.982.247-25\n"  # sem ':' -> nao rotulado, mas e o CPF
        "Maria Silva"
    )
    parsed = cf.parse_message(text)
    assert not parsed.get("number")  # nada do CPF capturado como numero


# ---------------------------------------------------------------------------
# Validacao por campo
# ---------------------------------------------------------------------------


def test_validate_field_rules():
    assert cf.validate_field("name", "Maria Silva") is True
    assert cf.validate_field("name", "Maria") is False
    assert cf.validate_field("document", VALID_CPF) is True
    assert cf.validate_field("document", "11111111111") is False
    assert cf.validate_field("postal_code", "01001000") is True
    assert cf.validate_field("postal_code", "010010") is False
    assert cf.validate_field("number", "930") is True
    assert cf.validate_field("complement", "") is True  # opcional


# ---------------------------------------------------------------------------
# Acumulacao — o requisito central (campo valido NUNCA repedido)
# ---------------------------------------------------------------------------


def test_merge_only_keeps_valid_fields():
    merged = cf.merge_collected({}, {"document": "11111111111", "postal_code": "01001000"})
    assert "document" not in merged  # CPF invalido nao entra
    assert merged["postal_code"] == "01001000"


def test_merge_never_overwrites_existing_valid_field():
    existing = {"postal_code": "01001000"}
    # Mesmo que venha outro CEP depois, o ja coletado e preservado.
    merged = cf.merge_collected(existing, {"postal_code": "99999999"})
    assert merged["postal_code"] == "01001000"


def test_valid_field_never_requested_again():
    """Medo do Caio: mandou o CEP uma vez, nunca mais e pedido."""
    state: dict = {}
    # 1a mensagem: CEP valido + numero
    state = cf.merge_collected(state, cf.parse_message("CEP: 01001-000\nNumero: 930"))
    assert state["postal_code"] == "01001000"
    assert state["number"] == "930"
    # 2a mensagem: cliente manda so o nome — CEP/numero seguem coletados.
    state = cf.merge_collected(state, cf.parse_message("Nome: Maria Silva"))
    pending = cf.pending_required(state)
    assert "postal_code" not in pending  # nunca repedido
    assert "number" not in pending
    assert pending == ["document"]  # so falta o CPF


def test_pending_required_reports_only_missing():
    state = {"name": "Maria Silva", "document": VALID_CPF}
    assert set(cf.pending_required(state)) == {"postal_code", "number"}


# ---------------------------------------------------------------------------
# Mensagens deterministicas
# ---------------------------------------------------------------------------


def test_build_reply_asks_only_missing():
    reply = cf.build_reply(["number"], [])
    assert "numero da casa" in reply
    assert "CEP" not in reply  # nao repede o que ja temos


def test_build_reply_flags_invalid_cpf_clearly():
    reply = cf.build_reply([], ["document"])
    assert "CPF" in reply and "nao parece valido" in reply


# ---------------------------------------------------------------------------
# advance_checkout (async) — fluxo completo com ViaCEP mockado
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_viacep(monkeypatch):
    async def fake_resolve(address, *, timeout=3.0, **kwargs):
        return {
            "postal_code": "01001000",
            "number": address.get("number", ""),
            "complement": address.get("complement", ""),
            "street": "Praca da Se",
            "district": "Se",
            "city": "Sao Paulo",
            "state": "SP",
        }

    monkeypatch.setattr(cf, "resolve_delivery_address", fake_resolve)


@pytest.mark.asyncio
async def test_advance_ready_when_all_in_one_message(_mock_viacep):
    text = (
        "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930"
    )
    turn = await cf.advance_checkout(text)
    assert turn.ready is True
    assert turn.collected["document"] == VALID_CPF
    assert turn.resolved_address["city"] == "Sao Paulo"
    assert turn.reply == ""  # nenhuma re-pergunta


@pytest.mark.asyncio
async def test_advance_asks_only_missing_then_completes(_mock_viacep):
    # 1o turno: faltou o numero.
    turn1 = await cf.advance_checkout(
        "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000"
    )
    assert turn1.ready is False
    assert "numero da casa" in turn1.reply
    # 2o turno: cliente manda so o numero; o resto segue coletado.
    turn2 = await cf.advance_checkout("Numero: 930", collected=turn1.collected)
    assert turn2.ready is True
    assert turn2.collected["postal_code"] == "01001000"


@pytest.mark.asyncio
async def test_advance_invalid_cpf_not_ready(_mock_viacep):
    turn = await cf.advance_checkout(
        "Nome: Maria Silva\nCPF: 111.111.111-11\nCEP: 01001-000\nNumero: 930"
    )
    assert turn.ready is False
    assert "CPF" in turn.reply
    assert "document" not in turn.collected


@pytest.mark.asyncio
async def test_advance_asks_address_when_viacep_fails(monkeypatch):
    async def fake_resolve(address, *, timeout=3.0, **kwargs):
        # ViaCEP nao resolveu — devolve so o que o cliente deu.
        return {"postal_code": "99999999", "number": address.get("number", "")}

    monkeypatch.setattr(cf, "resolve_delivery_address", fake_resolve)
    turn = await cf.advance_checkout(
        "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 99999-999\nNumero: 930"
    )
    assert turn.ready is False
    # Pede os campos que o ViaCEP nao trouxe, sem repedir CEP/numero.
    assert "rua" in turn.reply or "cidade" in turn.reply
    assert "CEP" not in turn.reply
