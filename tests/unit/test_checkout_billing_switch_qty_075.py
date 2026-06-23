"""Story-075: preservar a quantidade ao trocar de meio de pagamento (cartao<->Pix).

Caso real (Fernando, 22/06): cliente decide 2 potes -> escolhe cartao (link gerado)
-> diz "prefiro pix" -> a Livia reperguntava "quantos potes?" e fechava com 1 (queda
de ticket 2->1). O fix espelha a quantidade decidida em state["last_quantity"], entao
a reentrada reaproveita a qty e NAO repergunta.
"""
from __future__ import annotations

import pytest

from zwaf.conversion.intelligence import analyze_message
from zwaf.core import team as team_module
from zwaf.core.team import ZWAFTeam

TENANT = "livia-raiz-vital"


class FakeTenant:
    tenant_id = TENANT
    payment = {
        "products": {
            "new-woman": {
                "product_id": "nw-001",
                "unit_price_tiers_pix_cents": [
                    {"min_qty": 1, "max_qty": 1, "unit_cents": 14900},
                    {"min_qty": 2, "max_qty": None, "unit_cents": 12800},
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
def _mock_payment(monkeypatch):
    calls: list[dict] = []

    def fake_make_generator(tenant_id, payment):
        async def generate(**kwargs):
            calls.append(dict(kwargs))
            meio = "link de cartao" if kwargs.get("billing_type") == "CREDIT_CARD" else "Pix"
            return f"{meio}: TOKEN-{kwargs.get('billing_type')}"

        return generate

    monkeypatch.setattr("zwaf.tools.payment.make_payment_link_generator", fake_make_generator)
    return calls


async def _handle(t, message, session_id):
    signal = analyze_message(message, tenant_id=TENANT)
    return await t._handle_checkout(
        message=message, phone="5511999990001", session_id=session_id,
        lead_id="lead-1", signal=signal,
    )


@pytest.mark.asyncio
async def test_card_to_pix_preserves_quantity(team, _mock_payment):
    """AC-1: 2 potes -> cartao -> 'prefiro pix' -> mantem 2, NAO repergunta a qty."""
    t, store = team
    # 1) decide 2 potes no cartao -> gera o link de cartao (checkout encerra).
    r1 = await _handle(t, "quero comprar 2 potes no cartao, pode mandar o link", "s1")
    assert _mock_payment[0]["billing_type"] == "CREDIT_CARD"
    assert _mock_payment[0]["quantity"] == 2
    assert store["s1"]["checkout"]["active"] is False
    assert store["s1"]["last_quantity"] == 2

    # 2) troca para Pix com frase macia (NAO dispara should_send_payment_link):
    # mesmo assim a qty decidida e preservada e a coleta reabre, sem anchor.
    r2 = await _handle(t, "prefiro pagar com pix", "s1")
    assert "comecar com 1" not in (r2 or "").lower()
    assert store["s1"]["checkout"]["quantity"] == 2
    assert store["s1"]["checkout"]["billing_type"] == "PIX"
    assert store["s1"]["checkout"]["active"] is True


@pytest.mark.asyncio
async def test_pix_to_card_preserves_quantity(team, _mock_payment):
    """AC-2: 2 potes no Pix (coleta ativa) -> troca p/ cartao -> qty preservada."""
    t, store = team
    await _handle(t, "quero comprar 2 potes, pode mandar o pix", "s2")
    assert store["s2"]["checkout"]["active"] is True
    assert store["s2"]["checkout"]["quantity"] == 2
    # troca para cartao durante a coleta -> gera o link de cartao com 2 potes.
    r = await _handle(t, "na verdade prefiro no cartao", "s2")
    assert _mock_payment[-1]["billing_type"] == "CREDIT_CARD"
    assert _mock_payment[-1]["quantity"] == 2


@pytest.mark.asyncio
async def test_anchor_still_fires_when_qty_undecided(team, _mock_payment):
    """AC-3: sem regressao — quando a qty AINDA nao foi decidida, ancora 2-vs-1."""
    t, store = team
    r = await _handle(t, "pode mandar o link de pagamento", "s3")
    assert "comecar com 1" in (r or "").lower()
    assert store["s3"]["pending_checkout"]["stage"] == "quantity"
