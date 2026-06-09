"""Story-040 FR-7: anti-loop de endereco — 2 falhas -> escala.

Testa offline (sem agno):
- contador por sessao/lead em address_attempts;
- a tool guarded grava address_attempts no sink so em falha de endereco;
- o helper puro _should_escalate_address (de core.team) decide escalar apos 2;
- escalate_to_human e disparado (mockado) na 2a falha.

CPF de teste valido e CEP publico de exemplo — sem PII real.
"""
from __future__ import annotations

import asyncio

import pytest

from zwaf.conversion import address_attempts
from zwaf.conversion.address_attempts import (
    ESCALATION_THRESHOLD,
    get_attempts,
    record_address_failure,
    reset_attempts,
    should_escalate,
)
from zwaf.conversion.payment_gate import make_guarded_payment_link_generator
from zwaf.core.team import _should_escalate_address

_VALID_TEST_CPF = "529.982.247-25"

# Apenas CEP+numero, ViaCEP indisponivel -> endereco incompleto (falha de endereco).
_ADDR_INCOMPLETE = {"postal_code": "01001-000", "number": "930"}

_PAY_CFG = {
    "products": {
        "new-woman": {
            "product_id": "nw-001",
            "unit_price_tiers_pix_cents": [{"min_qty": 1, "max_qty": 1, "unit_cents": 14900}],
        }
    }
}


async def _viacep_none(cep, *, timeout=3.0):
    return None


@pytest.fixture(autouse=True)
def _clean_counter():
    address_attempts.clear_all()
    yield
    address_attempts.clear_all()


# --------------------------------------------------------------- counter ----

def test_counter_increments_per_session():
    assert get_attempts("s1", "l1") == 0
    assert record_address_failure("s1", "l1") == 1
    assert record_address_failure("s1", "l1") == 2
    assert get_attempts("s1", "l1") == 2


def test_counter_isolated_between_sessions():
    record_address_failure("s1", "l1")
    record_address_failure("s2", "l2")
    assert get_attempts("s1", "l1") == 1
    assert get_attempts("s2", "l2") == 1


def test_should_escalate_after_threshold():
    record_address_failure("s1", "l1")
    assert not should_escalate("s1", "l1")  # 1a falha
    record_address_failure("s1", "l1")
    assert should_escalate("s1", "l1")  # 2a falha
    assert ESCALATION_THRESHOLD == 2


def test_reset_clears_counter():
    record_address_failure("s1", "l1")
    record_address_failure("s1", "l1")
    reset_attempts("s1", "l1")
    assert get_attempts("s1", "l1") == 0
    assert not should_escalate("s1", "l1")


# ----------------------------------------------- gate records attempts ----

def test_gate_records_address_attempts_on_address_failure():
    sink: dict = {}
    gen = make_guarded_payment_link_generator(
        "livia-raiz-vital", _PAY_CFG, result_sink=sink,
        session_id="sg1", lead_id="lg1",
    )
    # Forca o resolver a nao completar (monkeypatch via viacep do resolver).
    # delivery_address incompleto + ViaCEP indisponivel -> falha de endereco.
    import zwaf.conversion.payment_gate as pg

    orig = pg.resolve_delivery_address

    async def _no_viacep(addr, **kw):
        return await orig(addr, viacep_resolver=_viacep_none)

    pg.resolve_delivery_address = _no_viacep
    try:
        reply = asyncio.run(gen(
            product_id="new-woman", customer_phone="5511999990000",
            customer_name="Maria Teste Silva", customer_document=_VALID_TEST_CPF,
            delivery_address=_ADDR_INCOMPLETE, buying_intent_evidence="quero pagar agora",
        ))
    finally:
        pg.resolve_delivery_address = orig

    assert "address_attempts" in sink
    assert sink["address_attempts"] == 1
    # 1a falha -> mensagem deterministica (nao escala ainda)
    assert not reply.startswith("http")
    assert get_attempts("sg1", "lg1") == 1


def test_gate_does_not_record_attempts_on_cpf_failure():
    # CPF invalido (035) NAO deve alimentar o anti-loop de endereco.
    sink: dict = {}
    gen = make_guarded_payment_link_generator(
        "livia-raiz-vital", _PAY_CFG, result_sink=sink,
        session_id="sc1", lead_id="lc1",
    )
    # endereco completo, CPF invalido
    full_addr = {
        "postal_code": "01001000", "street": "Praca da Se", "number": "10",
        "district": "Se", "city": "Sao Paulo", "state": "SP",
    }
    reply = asyncio.run(gen(
        product_id="new-woman", customer_phone="5511999990000",
        customer_name="Maria Teste Silva", customer_document="33143853123",
        delivery_address=full_addr, buying_intent_evidence="quero pagar agora",
    ))
    assert "address_attempts" not in sink
    assert "CPF" in reply  # mensagem 035 intacta
    assert get_attempts("sc1", "lc1") == 0


# -------------------------------------- escalation decision (team helper) ----

def test_should_escalate_address_requires_attempts_key():
    # Sem a chave address_attempts (ex.: falha de CPF) -> nunca escala.
    record_address_failure("se1", "le1")
    record_address_failure("se1", "le1")
    assert _should_escalate_address({}, "se1", "le1") is False
    # Com a chave + threshold atingido -> escala.
    assert _should_escalate_address({"address_attempts": 2}, "se1", "le1") is True


def test_should_escalate_address_first_failure_no_escalation():
    record_address_failure("se2", "le2")
    assert _should_escalate_address({"address_attempts": 1}, "se2", "le2") is False


def test_two_address_failures_trigger_escalation_path():
    # AC-6: 2 falhas na mesma conversa -> should_escalate True.
    record_address_failure("af1", "al1")
    first = _should_escalate_address({"address_attempts": 1}, "af1", "al1")
    record_address_failure("af1", "al1")
    second = _should_escalate_address({"address_attempts": 2}, "af1", "al1")
    assert first is False
    assert second is True


def test_escalate_to_human_callable_returns_transition_message():
    # AC-6: a tool de escala devolve mensagem de transicao ao cliente.
    from zwaf.tools.escalation import escalate_to_human

    msg = asyncio.run(escalate_to_human(
        lead_phone="5511999990000",
        lead_name="Cliente",
        problem_summary="Checkout travado: endereco apos 2 tentativas (anti-loop).",
        conversation_history="01001-000, 930",
    ))
    assert isinstance(msg, str) and msg
