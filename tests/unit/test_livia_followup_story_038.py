"""Story 038: Livia follow-up and supervised self-improvement tests."""
from __future__ import annotations

import pytest

from zwaf.conversion.followup import (
    FollowupStage,
    LeadTemperature,
    build_followup_plan,
    classify_lead_temperature,
)
from zwaf.conversion.funnel_events import FunnelEventName, build_funnel_event
from zwaf.conversion.self_improvement import (
    ImprovementKind,
    ImprovementQueue,
    ImprovementStatus,
    is_live_change_allowed_without_approval,
)
from zwaf.reporting.commercial_report import format_commercial_daily_report, send_commercial_daily_report
from zwaf.tools.notifications import alert_operator


def test_lead_temperature_hot_with_two_strong_signals():
    result = classify_lead_temperature("Tenho muito calor e dor. Qual o preco?")

    assert result.temperature is LeadTemperature.HOT
    assert "diagnosis_real_pain" in result.strong_signals
    assert "asked_price" in result.strong_signals


def test_lead_temperature_hot_with_final_checkout_signal():
    result = classify_lead_temperature("Pode mandar o link no pix")

    assert result.temperature is LeadTemperature.HOT
    assert "requested_link" in result.final_signals


def test_lead_temperature_warm_cold_and_risk():
    assert classify_lead_temperature("Qual o valor?").temperature is LeadTemperature.WARM
    assert classify_lead_temperature("Oi, tudo bem?").temperature is LeadTemperature.COLD
    assert classify_lead_temperature("Tomo remedio e tive reacao").temperature is LeadTemperature.RISK


def test_hot_followup_post_offer_schedules_max_five():
    plan = build_followup_plan(
        messages="Tenho sintomas fortes e quero comprar",
        stage=FollowupStage.POST_OFFER,
    )

    assert plan.allowed is True
    assert plan.temperature is LeadTemperature.HOT
    assert plan.max_contacts == 5
    assert [contact.delay_hours for contact in plan.contacts] == [1, 24, 48, 96, 168]


def test_warm_cold_and_resistant_limits():
    warm = build_followup_plan(messages="Qual o valor?", stage="post_offer")
    cold = build_followup_plan(messages="Oi", stage="post_offer")
    resistant = build_followup_plan(
        messages="Tenho sintomas e perguntei do frete, mas vou pensar",
        stage="checkout_incomplete",
        dry_or_resistant=True,
    )

    assert warm.max_contacts == 3
    assert cold.max_contacts == 1
    assert resistant.max_contacts == 2


def test_followup_stops_for_opt_out_and_medical_risk():
    opt_out = build_followup_plan(messages="Nao tenho interesse, pode remover", stage="post_offer")
    risk = build_followup_plan(messages="Tive alergia e tomo medicamento", stage="post_offer")

    assert opt_out.allowed is False
    assert opt_out.reason == "opt_out"
    assert risk.allowed is False
    assert risk.reason == "medical_risk"


def test_funnel_event_uses_hash_and_strips_pii():
    event = build_funnel_event(
        tenant_id="livia-raiz-vital",
        event=FunnelEventName.CHECKOUT_REQUESTED,
        session_id="session-lead-001",
        metadata={
            "stage": "post_offer",
            "phone": "wa-local-placeholder",
            "email": "cliente@example.com",
            "objection": "preco",
            "token": "secret",
        },
    ).to_dict()

    assert event["session_hash"] != "session-lead-001"
    assert len(event["session_hash"]) == 64
    assert event["metadata"] == {"stage": "post_offer", "objection": "preco"}


def test_improvement_candidates_do_not_promote_without_approval():
    queue = ImprovementQueue()
    candidate = queue.suggest(
        kind=ImprovementKind.COPY,
        summary="Nova copy para follow-up de preco",
        evidence={"payment_confirmed_rate": 0.12},
    )

    assert candidate.status is ImprovementStatus.SUGGESTED
    assert queue.pending() == [candidate]
    assert is_live_change_allowed_without_approval(ImprovementKind.COPY) is False
    assert is_live_change_allowed_without_approval(ImprovementKind.OPERATIONAL) is True
    with pytest.raises(ValueError):
        queue.review(candidate_id=candidate.id, status=ImprovementStatus.PROMOTED, actor="qa")

    approved = queue.review(candidate_id=candidate.id, status=ImprovementStatus.APPROVED, actor="axis")
    promoted = queue.review(candidate_id=approved.id, status=ImprovementStatus.PROMOTED, actor="pixel")

    assert promoted.status is ImprovementStatus.PROMOTED
    assert len(queue.review_log()) == 2


def test_commercial_report_is_aggregate_and_redacts_pii():
    message = format_commercial_daily_report(
        {
            "leads_attended": 12,
            "hot_leads": 4,
            "checkouts_requested": 3,
            "links_generated": 3,
            "payments_confirmed": 2,
            "pots_sold": 5,
            "estimated_revenue_cents": 64000,
            "offer_to_checkout_rate": 0.3,
            "checkout_to_payment_rate": 0.66,
            "pots_per_paid_order": 2.5,
            "followups_sent": 7,
            "followups_replied": 3,
            "followups_to_checkout": 2,
            "followups_to_payment": 1,
            "top_objections": ["preco", "entrega", "cliente@example.com"],
            "actions": {
                "testimonial_to_request": "Maria Silva - Rua das Flores 123",
                "human_needed": "lead-session-42",
                "operational_problem": "nenhum",
            },
        }
    )

    assert "Pagamentos confirmados: 2" in message
    assert "R$ 640,00" in message
    assert "cliente@example.com" not in message
    assert "Maria Silva" not in message
    assert "Rua das Flores" not in message
    assert "lead-session-42" in message


@pytest.mark.asyncio
async def test_notifications_use_local_env_without_versioned_phone(monkeypatch):
    calls = []

    class FakeWhatsApp:
        async def send_message(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.delenv("OPERATOR_PERSONAL_WHATSAPP", raising=False)
    assert await alert_operator(text="gate atingido", whatsapp_tool=FakeWhatsApp()) is False

    monkeypatch.setenv("OPERATOR_PERSONAL_WHATSAPP", "wa-local-operator")
    assert await alert_operator(text="gate atingido", whatsapp_tool=FakeWhatsApp()) is True
    assert calls[0]["phone"] == "wa-local-operator"


@pytest.mark.asyncio
async def test_commercial_report_sends_to_fernando_env(monkeypatch):
    calls = []

    class FakeWhatsApp:
        async def send_message(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setenv("FERNANDO_WHATSAPP", "wa-local-fernando")
    sent = await send_commercial_daily_report({"leads_attended": 1}, FakeWhatsApp())

    assert sent is True
    assert calls[0]["phone"] == "wa-local-fernando"
    assert "Resumo diario" in calls[0]["text"]
