"""Story-073: responder duvida do cliente durante a coleta de checkout.

Unit (heuristica pura) + integracao no ZWAFTeam com o LLM mockado.
CPF de teste valido; sem PII real.
"""
from __future__ import annotations

import pytest

from zwaf.conversion import checkout_flow as cf
from zwaf.conversion.intelligence import analyze_message
from zwaf.core import team as team_module
from zwaf.core.team import ZWAFTeam

TENANT = "livia-raiz-vital"
DATA_MSG = "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930"


# ----------------------------------------------------- heuristica pura ------


def test_is_checkout_question_detects_questions():
    for msg in [
        "como devo tomar?",
        "quantas capsulas por dia?",
        "qual a melhor forma de tomar",
        "tem efeito colateral?",
        "quanto tempo demora pra entregar?",
        "posso pagar depois?",
    ]:
        assert cf.is_checkout_question(msg) is True, msg


def test_is_checkout_question_ignores_data_messages():
    # AC-3: mensagem com dado de checkout nunca e tratada como pergunta.
    for msg in [DATA_MSG, "Numero: 930", "01001-000", "529.982.247-25",
                "Joao Carlos Pereira\n11144477735\n01001000\n167"]:
        assert cf.is_checkout_question(msg) is False, msg


def test_is_checkout_question_ignores_plain_statements():
    for msg in ["pode mandar o link", "manda o pix", "ok", "prefiro pix",
                "na verdade quero no cartao"]:
        assert cf.is_checkout_question(msg) is False, msg


def test_resume_hint_asks_only_missing():
    hint = cf.build_checkout_resume_hint({"name": "Maria Silva"})
    assert "CPF" in hint
    assert "nome" not in hint.lower()


def test_resume_hint_when_all_collected():
    hint = cf.build_checkout_resume_hint(
        {"name": "Maria Silva", "document": "52998224725",
         "postal_code": "01001000", "number": "930"}
    )
    assert "confirmar" in hint.lower()


# -------------------------------------------------- integracao no team ------


class FakeTenant:
    tenant_id = TENANT
    payment = {
        "products": {
            "new-woman": {
                "product_id": "nw-001",
                "unit_price_tiers_pix_cents": [
                    {"min_qty": 1, "max_qty": None, "unit_cents": 14900}
                ],
            }
        }
    }


@pytest.fixture
def team(monkeypatch):
    store: dict = {}

    async def fake_get(session_id, tenant_id):
        return dict(store.get(session_id, {}))

    async def fake_set(session_id, tenant_id, state, ttl_seconds=3600):
        store[session_id] = dict(state)

    monkeypatch.setattr(team_module, "get_session_state", fake_get)
    monkeypatch.setattr(team_module, "set_session_state", fake_set)

    t = ZWAFTeam(tenant_config=FakeTenant(), whatsapp_tool=None, router=None)
    return t, store


async def _handle(t, message, session_id):
    signal = analyze_message(message, tenant_id=TENANT)
    return await t._handle_checkout(
        message=message, phone="5511999990001", session_id=session_id,
        lead_id="lead-1", signal=signal,
    )


@pytest.mark.asyncio
async def test_question_during_checkout_is_answered_not_dry_reask(team, monkeypatch):
    """AC-1: pergunta durante a coleta -> responde a duvida (LLM/knowledge), nao
    apenas 'preciso dos dados'."""
    t, store = team

    async def fake_run_agent(**kwargs):
        return "Recomendo 2 capsulas por dia, de preferencia pela manha."

    monkeypatch.setattr(t, "_run_agent", fake_run_agent)

    await _handle(t, "quero comprar 2 potes, pode mandar o pix", "qa1")
    assert store["qa1"]["checkout"]["active"] is True

    reply = await _handle(t, "como devo tomar?", "qa1")
    assert "2 capsulas por dia" in reply
    # retomada gentil da coleta (nao a mensagem seca de coleta)
    assert "finaliz" in reply.lower()
    # AC-2: checkout segue ativo e os campos preservados
    assert store["qa1"]["checkout"]["active"] is True


@pytest.mark.asyncio
async def test_question_preserves_collected_fields(team, monkeypatch):
    """AC-2: a pergunta nao apaga os dados ja coletados; a coleta continua."""
    t, store = team

    async def fake_run_agent(**kwargs):
        return "Pode tomar com agua, em jejum ou nao."

    monkeypatch.setattr(t, "_run_agent", fake_run_agent)

    await _handle(t, "quero comprar 2 potes, pode mandar o pix", "qa2")
    # cliente ja mandou parte dos dados
    await _handle(t, "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000", "qa2")
    fields_before = dict(store["qa2"]["checkout"]["fields"])
    assert fields_before.get("name") == "Maria Silva"

    # agora faz uma pergunta
    reply = await _handle(t, "posso tomar a noite?", "qa2")
    assert "agua" in reply.lower()
    # campos preservados
    assert store["qa2"]["checkout"]["fields"]["name"] == "Maria Silva"
    assert store["qa2"]["checkout"]["active"] is True
    # a retomada pede so o que falta (numero)
    assert "numero" in reply.lower()


@pytest.mark.asyncio
async def test_data_message_still_collected_no_regression(team, monkeypatch):
    """AC-3: mensagem com dados continua sendo coleta (sem rota de pergunta)."""
    t, store = team
    called = {"n": 0}

    async def fake_run_agent(**kwargs):
        called["n"] += 1
        return "resposta"

    monkeypatch.setattr(t, "_run_agent", fake_run_agent)

    await _handle(t, "quero comprar 2 potes, pode mandar o pix", "qa3")
    reply = await _handle(t, "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000", "qa3")
    assert called["n"] == 0  # nao roteou pra pergunta
    assert "numero da casa" in reply  # coleta deterministica normal
