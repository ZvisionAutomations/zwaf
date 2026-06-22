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
    reply = await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "s1")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "s4")
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

    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "pii1")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "s5")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "s7")
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

    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "lock1")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "s6")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "h1")
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
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "h2")
    reply = await _signal_handle(t, "isso e um golpe, vou no procon", "h2")
    assert "Fernando" in reply
    assert store["h2"]["checkout"]["active"] is False
    assert _mock_escalation


@pytest.mark.asyncio
async def test_valid_data_during_checkout_does_not_escalate(team, _mock_viacep, _mock_pix, _mock_escalation):
    """Regressao: dados do formulario nunca casam padroes criticos — sem escala."""
    t, store = team
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "h3")
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


# ─── Story-046: ancora 2-vs-1, captura robusta de quantidade e escolha de meio ──


class TieredTenant:
    tenant_id = TENANT
    payment = {
        "products": {
            "new-woman": {
                "product_id": "nw-001",
                "unit_price_tiers_pix_cents": [
                    {"min_qty": 1, "max_qty": 1, "unit_cents": 14900},
                    {"min_qty": 2, "max_qty": 4, "unit_cents": 12800},
                    {"min_qty": 5, "max_qty": None, "unit_cents": 11990},
                ],
            }
        }
    }


@pytest.fixture
def team_tiered(monkeypatch):
    store: dict = {}

    async def fake_get(session_id, tenant_id):
        return dict(store.get(session_id, {}))

    async def fake_set(session_id, tenant_id, state, ttl_seconds=3600):
        store[session_id] = dict(state)

    monkeypatch.setattr(team_module, "get_session_state", fake_get)
    monkeypatch.setattr(team_module, "set_session_state", fake_set)

    t = ZWAFTeam(tenant_config=TieredTenant(), whatsapp_tool=None, router=None)
    return t, store


def test_quantity_detection_buy_context_and_spelled():
    """AC-2: numero colado a compra/'potes' e por extenso vira quantidade."""
    q = team_module._quantity_in_message
    assert q("quero 2 gata") == 2
    assert q("quero dois") == 2
    assert q("vou levar tres") == 3
    assert q("manda 2") == 2
    assert q("me ve 3 potes") == 3
    assert q("quero comprar 5 potes") == 5
    # MED-1: forma por extenso ACENTUADA ("tres" com acento) tambem deve casar.
    assert q("quero três") == 3
    assert q("três potes") == 3
    assert q("vou levar três") == 3


def test_quantity_detection_guards_false_positives():
    """AC-3/AC-4: CEP, numero da casa, CPF, duracao e parcelamento NAO viram quantidade."""
    q = team_module._quantity_in_message
    assert q("Numero: 930") is None
    assert q("01001-000") is None
    assert q("529.982.247-25") is None
    assert q(DATA_MSG) is None
    assert q("faz 2 anos que tenho calor") is None
    assert q("ha 3 meses") is None
    assert q("quero parcelar em 2 vezes") is None
    assert q("quero em 3x") is None


@pytest.mark.asyncio
async def test_anchor_quantity_when_undecided(team):
    """AC-5: gatilho sem quantidade decidida ancora 2-vs-1 antes de coletar."""
    t, store = team
    reply = await _signal_handle(t, "quero o pix", "anc1")
    low = reply.lower()
    assert "comecar com 1" in low or "ciclo" in low
    assert store["anc1"]["pending_checkout"]["stage"] == "quantity"
    assert not store["anc1"].get("checkout", {}).get("active")
    # responde a quantidade; como o meio (pix) ja foi dito, vai direto pra coleta
    reply2 = await _signal_handle(t, "pode ser 2", "anc1")
    assert "Nome:" in reply2 and "2 potes" in reply2
    assert store["anc1"]["checkout"]["active"] is True
    assert store["anc1"]["checkout"]["quantity"] == 2
    assert store["anc1"]["checkout"]["billing_type"] == "PIX"


@pytest.mark.asyncio
async def test_asks_payment_method_when_undecided(team):
    """AC-7: quantidade decidida e meio indefinido -> pergunta cartao/Pix antes da coleta."""
    t, store = team
    await _signal_handle(t, "quero 2 potes", "pm1")  # decide a quantidade (sem gatilho)
    reply = await _signal_handle(t, "pode mandar o link", "pm1")
    low = reply.lower()
    assert "cartao" in low and "pix" in low
    assert store["pm1"]["pending_checkout"]["stage"] == "billing"
    assert not store["pm1"].get("checkout", {}).get("active")
    reply2 = await _signal_handle(t, "no pix mesmo", "pm1")
    assert "Nome:" in reply2 and "2 potes" in reply2
    assert store["pm1"]["checkout"]["billing_type"] == "PIX"
    assert store["pm1"]["checkout"]["quantity"] == 2


@pytest.mark.asyncio
async def test_already_signaled_method_skips_question(team, _mock_pix):
    """AC-7 + story-048: se a cliente ja disse o meio (cartao) e a quantidade, nao
    repergunta — vai direto pro checkout HOSPEDADO (sem coletar dados no chat)."""
    t, store = team
    reply = await _signal_handle(t, "quero comprar 2 potes no cartao", "pm2")
    assert reply == "Pix copia e cola: 00020126XYZ"  # gerador (mock) chamado
    assert "Nome:" not in reply  # cartao nao coleta no chat (vai pro hospedado)
    assert store["pm2"]["checkout"]["billing_type"] == "CREDIT_CARD"
    assert store["pm2"]["checkout"]["active"] is False
    assert "pending_checkout" not in store["pm2"]
    assert _mock_pix["billing_type"] == "CREDIT_CARD"


@pytest.mark.asyncio
async def test_loma_quantity_change_reconfirms_new_total(team_tiered, _mock_viacep, _mock_pix):
    """AC-1 (caso Loma): muda 1->2 no meio da coleta, re-confirma R$256 e cobra certo."""
    t, store = team_tiered
    await _signal_handle(t, "quero comprar 1 pote, pode mandar o pix", "loma")
    assert store["loma"]["checkout"]["quantity"] == 1
    reply = await _signal_handle(t, "Quero 2 gata", "loma")
    assert "2 potes" in reply and "256,00" in reply  # re-confirmacao do novo total
    assert store["loma"]["checkout"]["quantity"] == 2
    assert store["loma"]["checkout"]["active"] is True  # ainda nao gerou
    reply2 = await _signal_handle(t, DATA_MSG, "loma")
    assert reply2 == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["quantity"] == 2


# ─── Pix em 2 mensagens: anuncio + codigo puro ──────────────


@pytest.mark.asyncio
async def test_send_response_splits_pix_into_two_messages():
    """O Pix sai em 2 mensagens: a 2a e SO o codigo (copiar sem texto junto)."""
    from zwaf.tools.payment import _pix_message

    sent: list[str] = []

    class FakeWA:
        async def send_message(self, phone, text, session_id):
            sent.append(text)

    t = ZWAFTeam(tenant_config=FakeTenant(), whatsapp_tool=FakeWA(), router=None)
    await t.send_response(phone="5511999990001", text=_pix_message("00020126CODIGO", 14900), session_id="s")

    assert len(sent) == 2
    assert "proxima mensagem" in sent[0].lower() or "vou te mandar" in sent[0].lower()
    assert sent[1] == "00020126CODIGO"  # 2a mensagem = SO o codigo, sem texto


@pytest.mark.asyncio
async def test_send_response_single_message_without_split():
    """Resposta sem separador continua sendo uma unica mensagem."""
    sent: list[str] = []

    class FakeWA:
        async def send_message(self, phone, text, session_id):
            sent.append(text)

    t = ZWAFTeam(tenant_config=FakeTenant(), whatsapp_tool=FakeWA(), router=None)
    await t.send_response(phone="5511999990001", text="oi, tudo bem?", session_id="s")
    assert sent == ["oi, tudo bem?"]


# ─── BUG-FIX: dados sem rotulo + troca de quantidade na coleta (caso Miguel) ──────────────

UNLABELED_DATA_MSG = "Miguel Augusto Oliveira\n53812532816\n06754060\n167"


@pytest.mark.asyncio
async def test_unlabeled_positional_data_generates_pix(team, _mock_viacep, _mock_pix):
    """Caso real Miguel: cliente copia os VALORES sem os rotulos -> deve funcionar.

    Antes: o nome nunca era extraido de texto livre -> loop 'faltou nome completo'.
    """
    t, store = team
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "m1")
    reply = await _signal_handle(t, UNLABELED_DATA_MSG, "m1")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Miguel Augusto Oliveira"
    assert _mock_pix["customer_document"] == "53812532816"


@pytest.mark.asyncio
async def test_unlabeled_name_alone_completes_checkout(team, _mock_viacep, _mock_pix):
    """Nome solto sem rotulo ('Miguel Augusto Oliveira') fecha a coleta quando so falta o nome."""
    t, store = team
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "m2")
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
    await _signal_handle(t, "quero comprar 1 pote, pode mandar o pix", "q9")  # ativa com qty 1
    await _signal_handle(t, "mas quero 2 potes", "q9")  # corrige no meio -> re-confirma
    assert store["q9"]["checkout"]["quantity"] == 2
    reply = await _signal_handle(t, UNLABELED_DATA_MSG, "q9")
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["quantity"] == 2


# ─── STORY-042: checkout de cartao ──────────────


@pytest.mark.asyncio
async def test_card_intent_activates_checkout_as_credit_card(team):
    """'quero pagar no cartao' gera link direto sem formulario de dados."""
    t, store = team
    reply = await _signal_handle(t, "quero comprar 2 potes no cartao", "c1")
    assert reply is not None
    assert "Nome:" not in reply
    assert "CPF:" not in reply
    assert "CEP:" not in reply
    assert store["c1"]["checkout"]["active"] is False
    assert store["c1"]["checkout"]["billing_type"] == "CREDIT_CARD"


@pytest.mark.asyncio
async def test_card_billing_reaches_generator(team, _mock_viacep, _mock_pix):
    """End-to-end: a escolha de cartao chega ao gerador como CREDIT_CARD."""
    t, store = team
    # cartao COM quantidade decidida -> gera o link hospedado direto (sem coletar dados)
    reply = await _signal_handle(t, "quero comprar 2 potes no cartao", "c2")
    assert reply == "Pix copia e cola: 00020126XYZ"  # gerador mockado
    assert _mock_pix["billing_type"] == "CREDIT_CARD"
    assert _mock_pix["customer_name"] == ""
    assert _mock_pix["customer_document"] == ""
    assert store["c2"]["checkout"]["active"] is False


@pytest.mark.asyncio
async def test_pix_remains_default_without_card_mention(team, _mock_viacep, _mock_pix):
    """Regressao: sem mencao a cartao, o meio continua PIX (maior conversao)."""
    t, _ = team
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "c3")
    await _signal_handle(t, DATA_MSG, "c3")
    assert _mock_pix["billing_type"] == "PIX"


@pytest.mark.asyncio
async def test_billing_switch_to_card_during_collection(team, _mock_viacep, _mock_pix):
    """Cliente comeca no Pix e troca para cartao no meio — respeita a ultima escolha."""
    t, store = team
    await _signal_handle(t, "quero comprar 2 potes, pode mandar o pix", "c4")
    # ainda coletando (Pix), cliente troca para cartao -> vai pro checkout hospedado
    reply = await _signal_handle(t, "na verdade quero no cartao\nNome: Maria Silva", "c4")
    assert store["c4"]["checkout"]["billing_type"] == "CREDIT_CARD"
    assert store["c4"]["checkout"]["active"] is False
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["billing_type"] == "CREDIT_CARD"
    assert _mock_pix["customer_name"] == ""


@pytest.mark.asyncio
async def test_card_preference_persisted_before_trigger(team, _mock_pix):
    """'prefiro cartao' antes do gatilho e lembrado; como falta a quantidade, o gate
    pergunta 2-vs-1 e lembra o meio (cartao). A resposta ativa o checkout hospedado
    (story-042/046/048)."""
    t, store = team
    await _signal_handle(t, "prefiro pagar no cartao", "c5")
    reply = await _signal_handle(t, "2 potes", "c5")
    assert store["c5"]["checkout"]["billing_type"] == "CREDIT_CARD"
    assert store["c5"]["checkout"]["quantity"] == 2
    assert reply == "Pix copia e cola: 00020126XYZ"  # cartao -> link hospedado (mock)
    assert "Nome:" not in reply
    assert _mock_pix["billing_type"] == "CREDIT_CARD"


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


# ---------------------------------------------------------------------------
# story-068: pushName -> confirmacao de nome (1 toque) e fallback CTWA
# ---------------------------------------------------------------------------


async def _signal_handle_push(t, message, session_id, push_name=""):
    signal = analyze_message(message, tenant_id=TENANT)
    return await t._handle_checkout(
        message=message, phone="5511999990001", session_id=session_id,
        lead_id="lead-1", signal=signal, push_name=push_name,
    )


@pytest.mark.asyncio
async def test_pushname_offers_one_tap_confirmation(team):
    """AC-1: lead com pushName valido confirma o nome com 1 pergunta, sem pedir do zero."""
    t, store = team
    reply = await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn1", push_name="joao pedro"
    )
    # pergunta de confirmacao (nao o formulario), com o nome sanitizado
    assert "Joao Pedro" in reply
    assert "Nome:" not in reply
    assert store["pn1"]["pending_checkout"]["stage"] == "name_confirm"
    assert store["pn1"].get("checkout", {}).get("active") is not True


@pytest.mark.asyncio
async def test_pushname_confirmed_skips_name_in_form(team):
    """AC-1/AC-3: confirmado -> transicao SEM 'Nome:' e nome sanitizado pre-preenchido."""
    t, store = team
    await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn2", push_name="MARIA silva"
    )
    reply = await _signal_handle_push(t, "sim, pode", "pn2")
    assert "Nome:" not in reply  # nao pede o nome de novo
    assert "Maria Silva" in reply
    assert "CPF:" in reply and "CEP:" in reply
    assert store["pn2"]["checkout"]["active"] is True
    assert store["pn2"]["checkout"]["fields"]["name"] == "Maria Silva"
    assert store["pn2"]["name_confirmed"] is True


@pytest.mark.asyncio
async def test_pushname_confirmed_name_goes_to_asaas(team, _mock_viacep, _mock_pix):
    """AC-4: o nome cobrado no Asaas e o CONFIRMADO (pushName), nao o que vier no form."""
    t, store = team
    await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn3", push_name="joao pedro"
    )
    await _signal_handle_push(t, "isso mesmo", "pn3")
    # cliente manda CPF/CEP/Numero (form sem Nome); mesmo se mandar outro nome,
    # o confirmado prevalece (merge nunca sobrescreve campo ja coletado).
    reply = await _signal_handle_push(
        t, "Nome: Fulano Trocado\nCPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930", "pn3"
    )
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Joao Pedro"


@pytest.mark.asyncio
async def test_ctwa_null_pushname_falls_back_to_asking_name(team):
    """AC-2: lead CTWA/@lid (pushName vazio) cai no fluxo de pedir o nome, sem erro."""
    t, store = team
    reply = await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn4", push_name=""
    )
    assert "Nome:" in reply  # formulario normal pedindo o nome
    assert store["pn4"]["checkout"]["active"] is True
    assert "push_name" not in store["pn4"]


@pytest.mark.asyncio
async def test_pushname_emoji_only_falls_back_to_asking_name(team):
    """AC-2/AC-3: pushName so com emoji sanitiza para "" -> pede o nome normalmente."""
    t, store = team
    reply = await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn5", push_name="🌸✨"
    )
    assert "Nome:" in reply
    assert "push_name" not in store["pn5"]


@pytest.mark.asyncio
async def test_pushname_rejected_asks_for_name(team):
    """AC-1: cliente recusa o nome proposto -> formulario pede o nome (sem autofill)."""
    t, store = team
    await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "pn6", push_name="joao pedro"
    )
    reply = await _signal_handle_push(t, "nao, prefiro outro nome", "pn6")
    assert "Nome:" in reply
    assert store["pn6"]["checkout"]["active"] is True
    assert store["pn6"]["checkout"]["fields"].get("name") in (None, "")


@pytest.mark.asyncio
async def test_pushname_encrypted_at_rest_in_session(team, _mock_viacep, _mock_pix, monkeypatch):
    """story-068 hardening: pushName nao fica em texto claro no session store quando
    ha Fernet key; round-trip ate o Pix preserva o nome confirmado."""
    monkeypatch.setenv("ZWAF_PII_FERNET_KEY", Fernet.generate_key().decode())
    t, store = team

    # 1o turno: oferece a confirmacao -> pushName persistido (cifrado) em pending_checkout
    await _signal_handle_push(
        t, "quero comprar 2 potes, pode mandar o pix", "enc1", push_name="joao pedro"
    )
    raw = json.dumps(store["enc1"], ensure_ascii=False)
    assert "Joao Pedro" not in raw  # nao vaza em texto claro
    assert store["enc1"]["pending_checkout"][team_module.PUSH_NAME_ENCRYPTED_FLAG] is True

    # confirma -> nome confirmado vai para fields (tambem cifrado) e gera o Pix
    await _signal_handle_push(t, "sim, pode", "enc1")
    raw2 = json.dumps(store["enc1"], ensure_ascii=False)
    assert "Joao Pedro" not in raw2
    reply = await _signal_handle_push(
        t, "CPF: 529.982.247-25\nCEP: 01001-000\nNumero: 930", "enc1"
    )
    assert reply == "Pix copia e cola: 00020126XYZ"
    assert _mock_pix["customer_name"] == "Joao Pedro"
