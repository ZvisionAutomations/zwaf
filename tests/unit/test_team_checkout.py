"""Integracao do modo checkout deterministico no ZWAFTeam (story-041 F2)."""
from __future__ import annotations

import asyncio
import json

import pytest
from cryptography.fernet import Fernet

from zwaf.conversion.intelligence import analyze_message
from zwaf.core import team as team_module
from zwaf.core.team import InventoryReservationSweepScheduler, ZWAFTeam

TENANT = "livia-raiz-vital"
VALID_CPF = "529.982.247-25"
DATA_MSG = "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930"
PARTIAL_DATA_MSG = "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000"


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

    monkeypatch.setattr("zwaf.conversion.checkout_flow.resolve_delivery_address", fake_resolve)


@pytest.fixture
def _mock_pix(monkeypatch):
    captured: dict = {}

    def fake_make_generator(tenant_id, payment):
        async def generate(**kwargs):
            captured.update(kwargs)
            return "Pix copia e cola: 00020126XYZ"

        return generate

    monkeypatch.setattr("zwaf.tools.payment.make_payment_link_generator", fake_make_generator)
    return captured


async def _signal_handle(t, message, session_id):
    signal = analyze_message(message, tenant_id=TENANT)
    return await t._handle_checkout(
        message=message, phone="5511999990001", session_id=session_id,
        lead_id="lead-1", signal=signal,
    )


@pytest.mark.asyncio
async def test_activates_on_buying_intent(team):
    t, store = team
    reply = await _signal_handle(t, "quero o pix", "s1")
    assert "Nome:" in reply and "CEP:" in reply  # mensagem de transicao rotulada
    assert store["s1"]["checkout"]["active"] is True
    assert store["s1"]["checkout"]["product_id"] == "new-woman"


@pytest.mark.asyncio
async def test_not_activated_without_intent(team):
    t, _ = team
    reply = await _signal_handle(t, "oi, tudo bem?", "s2")
    assert reply is None  # deixa o LLM seguir


@pytest.mark.asyncio
async def test_extracts_quantity_from_trigger(team):
    t, store = team
    await _signal_handle(t, "quero comprar 3 potes, pode mandar o pix", "s3")
    assert store["s3"]["checkout"]["quantity"] == 3


@pytest.mark.asyncio
async def test_full_collection_generates_pix(team, _mock_viacep, _mock_pix):
    t, store = team
    await _signal_handle(t, "quero o pix", "s4")
    reply = await _signal_handle(t, DATA_MSG, "s4")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert store["s4"]["checkout"]["active"] is False
    # dados certos chegaram ao gerador
    assert _mock_pix["customer_name"] == "Maria Silva"
    assert _mock_pix["billing_type"] == "PIX"
    assert _mock_pix["delivery_address"]["city"] == "Sao Paulo"


@pytest.mark.asyncio
async def test_checkout_fields_are_encrypted_in_session_and_decrypted_for_pix(
    team,
    _mock_viacep,
    _mock_pix,
    monkeypatch,
):
    """STORY-043 AC-1: Redis payload hides checkout PII and round-trips to Pix."""
    monkeypatch.setenv("ZWAF_PII_FERNET_KEY", Fernet.generate_key().decode())
    t, store = team

    await _signal_handle(t, "quero o pix", "pii1")
    reply = await _signal_handle(t, PARTIAL_DATA_MSG, "pii1")

    assert "numero da casa" in reply
    raw_payload = json.dumps(store["pii1"], ensure_ascii=False)
    assert "Maria Silva" not in raw_payload
    assert "52998224725" not in raw_payload
    assert "01001000" not in raw_payload
    assert store["pii1"]["checkout"]["fields"][team_module.CHECKOUT_FIELDS_ENCRYPTED_FLAG] is True

    reply = await _signal_handle(t, "Numero: 930", "pii1")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Maria Silva"
    assert _mock_pix["customer_document"] == "52998224725"
    assert _mock_pix["delivery_address"]["number"] == "930"


@pytest.mark.asyncio
async def test_never_reasks_valid_field_across_turns(team, _mock_viacep, _mock_pix):
    t, store = team
    await _signal_handle(t, "quero o pix", "s5")
    # 1o turno: faltou o numero
    r1 = await _signal_handle(t, "Nome: Maria Silva\nCPF: 529.982.247-25\nCEP: 01001-000", "s5")
    assert "numero da casa" in r1
    assert "CEP" not in r1  # nunca repede o que ja temos
    # 2o turno: so o numero -> gera o Pix
    r2 = await _signal_handle(t, "Numero: 930", "s5")
    assert r2 == "Pix copia e cola: 00020126XYZ"


@pytest.mark.asyncio
async def test_double_send_does_not_regenerate(team, _mock_viacep, _mock_pix):
    """NFR-5: reenviar os mesmos dados nao gera um segundo Pix (checkout encerrado)."""
    t, store = team
    await _signal_handle(t, "quero o pix", "s7")
    r1 = await _signal_handle(t, DATA_MSG, "s7")
    assert r1 == "Pix copia e cola: 00020126XYZ"
    assert store["s7"]["checkout"]["active"] is False
    # Mesma mensagem de dados de novo: checkout ja encerrado, cai no fluxo normal.
    r2 = await _signal_handle(t, DATA_MSG, "s7")
    assert r2 is None


@pytest.mark.asyncio
async def test_concurrent_checkout_finalization_generates_single_pix(
    team,
    _mock_viacep,
    monkeypatch,
):
    """STORY-043 AC-3: session Redis lock prevents duplicate concurrent Pix generation."""
    t, store = team
    calls = 0
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_acquire(**kwargs):
        return not first_started.is_set()

    async def fake_release(**kwargs):
        return None

    def fake_make_generator(tenant_id, payment):
        async def generate(**kwargs):
            nonlocal calls
            calls += 1
            first_started.set()
            await release_first.wait()
            return "Pix copia e cola: 00020126XYZ"

        return generate

    monkeypatch.setattr(team_module, "acquire_session_lock", fake_acquire)
    monkeypatch.setattr(team_module, "release_session_lock", fake_release)
    monkeypatch.setattr("zwaf.tools.payment.make_payment_link_generator", fake_make_generator)

    await _signal_handle(t, "quero o pix", "lock1")
    first = asyncio.create_task(_signal_handle(t, DATA_MSG, "lock1"))
    await first_started.wait()
    second = asyncio.create_task(_signal_handle(t, DATA_MSG, "lock1"))
    await asyncio.sleep(0)
    release_first.set()

    replies = await asyncio.gather(first, second)

    assert calls == 1
    assert replies.count("Pix copia e cola: 00020126XYZ") == 1
    assert any("ja esta sendo gerado" in reply for reply in replies)
    assert store["lock1"]["checkout"]["active"] is False


@pytest.mark.asyncio
async def test_safety_net_gives_up_after_max_attempts(team):
    t, store = team
    await _signal_handle(t, "quero o pix", "s6")
    last = ""
    for _ in range(team_module.MAX_CHECKOUT_ATTEMPTS):
        last = await _signal_handle(t, "nao entendi", "s6")
    assert "passo a passo" in last
    assert store["s6"]["checkout"]["active"] is False


# ─── HIGH-1: escalacao de sinal critico durante o checkout ──────────────


@pytest.fixture
def _mock_escalation(monkeypatch):
    captured: dict = {}

    async def fake_escalate(**kwargs):
        captured.update(kwargs)
        return "Estou chamando o Fernando agora para te ajudar pessoalmente."

    async def fake_get_lead(**kwargs):
        return {"name": "Maria"}

    monkeypatch.setattr("zwaf.tools.escalation.escalate_to_human", fake_escalate)
    monkeypatch.setattr("zwaf.memory.lead_store.get_lead", fake_get_lead)
    return captured


@pytest.mark.asyncio
async def test_health_risk_during_checkout_escalates(team, _mock_escalation):
    """HIGH-1: 'passando mal' no meio da coleta sai do checkout e escala."""
    t, store = team
    await _signal_handle(t, "quero o pix", "h1")
    assert store["h1"]["checkout"]["active"] is True
    reply = await _signal_handle(t, "estou passando mal depois de tomar", "h1")
    assert "Fernando" in reply
    assert store["h1"]["checkout"]["active"] is False  # saiu do modo checkout
    assert _mock_escalation  # escalou ao humano
    assert "HEALTH_RISK" in _mock_escalation["problem_summary"]


@pytest.mark.asyncio
async def test_critical_complaint_during_checkout_escalates(team, _mock_escalation):
    """HIGH-1: reclamacao grave (golpe/procon) durante a coleta escala."""
    t, store = team
    await _signal_handle(t, "quero o pix", "h2")
    reply = await _signal_handle(t, "isso e um golpe, vou no procon", "h2")
    assert "Fernando" in reply
    assert store["h2"]["checkout"]["active"] is False
    assert _mock_escalation


@pytest.mark.asyncio
async def test_valid_data_during_checkout_does_not_escalate(team, _mock_viacep, _mock_pix, _mock_escalation):
    """Regressao: dados do formulario nunca casam padroes criticos — sem escala."""
    t, store = team
    await _signal_handle(t, "quero o pix", "h3")
    reply = await _signal_handle(t, DATA_MSG, "h3")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert not _mock_escalation  # nao escalou — seguiu o checkout normal


# ─── HIGH-2: quantidade persistida entre mensagens ──────────────


@pytest.mark.asyncio
async def test_quantity_persisted_from_earlier_message(team):
    """HIGH-2: qty dita antes ('quero 3 potes') vale no gatilho sem numero."""
    t, store = team
    r0 = await _signal_handle(t, "quero 3 potes", "q1")
    assert r0 is None  # ainda nao ativa o checkout
    assert store["q1"]["last_quantity"] == 3
    reply = await _signal_handle(t, "pode mandar o pix", "q1")
    assert store["q1"]["checkout"]["quantity"] == 3
    assert "3 potes" in reply  # transicao confirma a quantidade (HIGH-2)


@pytest.mark.asyncio
async def test_persisted_quantity_reaches_pix(team, _mock_viacep, _mock_pix):
    """HIGH-2 end-to-end: a quantidade lembrada chega ao gerador do Pix."""
    t, store = team
    await _signal_handle(t, "quero 3 potes", "q2")
    await _signal_handle(t, "pode mandar o pix", "q2")
    reply = await _signal_handle(t, DATA_MSG, "q2")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["quantity"] == 3


@pytest.mark.asyncio
async def test_quantity_in_trigger_overrides_persisted(team):
    """A quantidade da propria mensagem-gatilho prevalece sobre a lembrada."""
    t, store = team
    await _signal_handle(t, "quero 2 potes", "q3")
    reply = await _signal_handle(t, "quero comprar 5 potes, pode mandar o pix", "q3")
    assert store["q3"]["checkout"]["quantity"] == 5
    assert "5 potes" in reply


# ─── BUG-FIX: dados sem rotulo + troca de quantidade na coleta (caso Miguel) ──────────────

UNLABELED_DATA_MSG = "Miguel Augusto Oliveira\n53812532816\n06754060\n167"


@pytest.mark.asyncio
async def test_unlabeled_positional_data_generates_pix(team, _mock_viacep, _mock_pix):
    """Caso real Miguel: cliente copia os VALORES sem os rotulos -> deve funcionar.

    Antes: o nome nunca era extraido de texto livre -> loop 'faltou nome completo'.
    """
    t, store = team
    await _signal_handle(t, "quero comprar", "m1")
    reply = await _signal_handle(t, UNLABELED_DATA_MSG, "m1")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Miguel Augusto Oliveira"
    assert _mock_pix["customer_document"] == "53812532816"


@pytest.mark.asyncio
async def test_unlabeled_name_alone_completes_checkout(team, _mock_viacep, _mock_pix):
    """Nome solto sem rotulo ('Miguel Augusto Oliveira') fecha a coleta quando so falta o nome."""
    t, store = team
    await _signal_handle(t, "quero comprar", "m2")
    # manda CPF/CEP/numero rotulados, falta o nome
    r1 = await _signal_handle(t, "CPF: 538.125.328-16\nCEP: 06754-060\nNumero: 167", "m2")
    assert "nome completo" in r1
    # agora so o nome, SEM rotulo
    r2 = await _signal_handle(t, "Miguel Augusto Oliveira", "m2")
    assert r2 == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Miguel Augusto Oliveira"


@pytest.mark.asyncio
async def test_command_phrase_not_captured_as_name(team):
    """Regressao: 'quero pagar agora' (3 palavras) NAO pode virar nome do cliente."""
    from zwaf.conversion.checkout_flow import _name_from_free_text
    assert _name_from_free_text("quero pagar agora") == ""
    assert _name_from_free_text("rua das flores") == ""
    assert _name_from_free_text("Miguel Augusto Oliveira") == "Miguel Augusto Oliveira"


@pytest.mark.asyncio
async def test_quantity_change_during_collection(team, _mock_viacep, _mock_pix):
    """Caso Miguel: 'mas quero 2 potes' no meio da coleta atualiza a quantidade."""
    t, store = team
    await _signal_handle(t, "quero comprar", "q9")  # ativa com qty 1
    await _signal_handle(t, "mas quero 2 potes", "q9")  # corrige no meio
    assert store["q9"]["checkout"]["quantity"] == 2
    reply = await _signal_handle(t, UNLABELED_DATA_MSG, "q9")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["quantity"] == 2


# ─── STORY-042: checkout de cartao ──────────────


@pytest.mark.asyncio
async def test_card_intent_activates_checkout_as_credit_card(team):
    """'quero pagar no cartao' ativa o checkout com billing_type CREDIT_CARD."""
    t, store = team
    reply = await _signal_handle(t, "quero pagar no cartao", "c1")
    assert reply is not None
    assert "Nome:" in reply  # entrou no formulario de coleta
    assert "cartao" in reply.lower()  # transicao confirma o meio
    assert store["c1"]["checkout"]["active"] is True
    assert store["c1"]["checkout"]["billing_type"] == "CREDIT_CARD"


@pytest.mark.asyncio
async def test_card_billing_reaches_generator(team, _mock_viacep, _mock_pix):
    """End-to-end: a escolha de cartao chega ao gerador como CREDIT_CARD."""
    t, store = team
    await _signal_handle(t, "quero pagar no cartao", "c2")
    reply = await _signal_handle(t, DATA_MSG, "c2")
    assert reply == "Pix copia e cola: 00020126XYZ"  # gerador mockado
    assert _mock_pix["billing_type"] == "CREDIT_CARD"
    assert store["c2"]["checkout"]["active"] is False


@pytest.mark.asyncio
async def test_pix_remains_default_without_card_mention(team, _mock_viacep, _mock_pix):
    """Regressao: sem mencao a cartao, o meio continua PIX (maior conversao)."""
    t, _ = team
    await _signal_handle(t, "quero o pix", "c3")
    await _signal_handle(t, DATA_MSG, "c3")
    assert _mock_pix["billing_type"] == "PIX"


@pytest.mark.asyncio
async def test_billing_switch_to_card_during_collection(team, _mock_viacep, _mock_pix):
    """Cliente comeca no Pix e troca para cartao no meio — respeita a ultima escolha."""
    t, store = team
    await _signal_handle(t, "quero o pix", "c4")
    # ainda coletando, cliente muda de ideia junto com parte dos dados
    await _signal_handle(t, "na verdade quero no cartao\nNome: Maria Silva", "c4")
    assert store["c4"]["checkout"]["billing_type"] == "CREDIT_CARD"
    reply = await _signal_handle(t, "CPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930", "c4")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["billing_type"] == "CREDIT_CARD"


@pytest.mark.asyncio
async def test_card_preference_persisted_before_trigger(team):
    """'prefiro cartao' antes do gatilho e lembrado quando o checkout ativa."""
    t, store = team
    r0 = await _signal_handle(t, "prefiro pagar no cartao", "c5")
    # "pagar no cartao" ja e intencao de compra (story-042) -> ativa direto
    assert store["c5"]["checkout"]["billing_type"] == "CREDIT_CARD"
    assert r0 is not None


@pytest.mark.asyncio
async def test_inventory_sweep_scheduler_calls_release_expired(monkeypatch):
    """STORY-043 AC-2 wire: scheduler job delegates to release_expired by tenant."""
    captured = {}

    async def fake_release_expired(**kwargs):
        captured.update(kwargs)
        return 2

    monkeypatch.setattr("zwaf.memory.inventory_store.release_expired", fake_release_expired)
    scheduler = InventoryReservationSweepScheduler("livia-raiz-vital")

    released = await scheduler._release_expired()

    assert released == 2
    assert captured == {"tenant_id": "livia-raiz-vital"}
