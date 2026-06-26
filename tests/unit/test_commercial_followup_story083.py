"""Unit tests for the intelligent follow-up safety envelope (story-083).

Covers: cold-start activation floor, send-time floor, segment allow-list,
per-round cap, dry-run, paid-order/reply cancellation, message variation and
safe first-name personalization (never echoes the encrypted symptom).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from zwaf.conversion import commercial_followup as mod
from zwaf.conversion.followup import FollowupStage, build_followup_plan
from zwaf.conversion.commercial_followup import (
    FollowupCandidate,
    run_commercial_followup_job,
    schedule_commercial_followups,
)

TENANT = "livia-raiz-vital"
DB_URL = "postgresql://zwaf:test@postgres:5432/zwaf"


async def _noop_sleep(_seconds: float) -> None:
    return None


# ----------------------- config / env readers -----------------------

def test_round_cap_default_and_clamp(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_CAP", raising=False)
    assert mod._round_cap() == mod.DEFAULT_ROUND_CAP  # 3
    monkeypatch.setenv("COMMERCIAL_FOLLOWUP_CAP", "25")
    assert mod._round_cap() == mod.MAX_ROUND_CAP  # clamped to 10
    monkeypatch.setenv("COMMERCIAL_FOLLOWUP_CAP", "0")
    assert mod._round_cap() == 1  # floor at 1
    monkeypatch.setenv("COMMERCIAL_FOLLOWUP_CAP", "lixo")
    assert mod._round_cap() == mod.DEFAULT_ROUND_CAP


def test_enabled_segments_default_excludes_post_offer(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_SEGMENTS", raising=False)
    enabled = mod._enabled_segments()
    assert enabled == {"checkout_incomplete", "post_link"}
    assert FollowupStage.POST_OFFER.value not in enabled
    assert FollowupStage.REPURCHASE.value not in enabled


def test_activation_floor_default_is_window(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_ACTIVATED_AT", raising=False)
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_WINDOW_HOURS", raising=False)
    now = datetime(2026, 6, 26, 14, 0, tzinfo=timezone.utc)
    floor = mod._activation_floor(now)
    assert floor == now - timedelta(hours=48)


def test_activation_floor_respects_marker(monkeypatch):
    now = datetime(2026, 6, 26, 14, 0, tzinfo=timezone.utc)
    # marker inside the window -> floor moves forward to the marker
    monkeypatch.setenv("COMMERCIAL_FOLLOWUP_ACTIVATED_AT", "2026-06-26T04:00:00+00:00")
    floor = mod._activation_floor(now)
    assert floor == datetime(2026, 6, 26, 4, 0, tzinfo=timezone.utc)


# ----------------------- send-time floor / segments -----------------------

@pytest.mark.asyncio
async def test_send_time_floor_never_schedules_in_past(monkeypatch):
    now = datetime(2026, 6, 26, 14, 0, tzinfo=timezone.utc)  # 11:00 BRT (business hour)
    candidate = FollowupCandidate(
        phone="5511999990010",
        stage=FollowupStage.CHECKOUT_INCOMPLETE,
        messages="cpf cep endereco",
        last_activity_at=now - timedelta(days=10),  # far in the past
    )
    captured: dict[str, Any] = {}

    async def fake_candidates(*_a, **_k):
        return [candidate]

    async def fake_optout(*_a, **_k):
        return False

    async def fake_upsert(_db, _tenant, _cand, *, plan, next_send_at):
        captured["next_send_at"] = next_send_at

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    monkeypatch.setattr(mod, "is_followup_opted_out", fake_optout)
    monkeypatch.setattr(mod, "upsert_scheduled_followup", fake_upsert)
    monkeypatch.setattr(mod, "_emit_event", lambda *_a, **_k: None)

    await schedule_commercial_followups(DB_URL, TENANT, now=now)
    assert captured["next_send_at"] >= now  # never in the past


@pytest.mark.asyncio
async def test_post_offer_segment_not_scheduled(monkeypatch):
    candidate = FollowupCandidate(
        phone="5511999990011",
        stage=FollowupStage.POST_OFFER,
        messages="qual o valor preco",
        last_activity_at=datetime.now(timezone.utc),
    )
    upserts: list[Any] = []

    async def fake_candidates(*_a, **_k):
        return [candidate]

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    monkeypatch.setattr(mod, "upsert_scheduled_followup", lambda *a, **k: upserts.append(a))
    monkeypatch.setattr(mod, "is_followup_opted_out", _async_false)
    monkeypatch.setattr(mod, "_emit_event", lambda *_a, **_k: None)

    result = await schedule_commercial_followups(DB_URL, TENANT)
    assert result == 0
    assert upserts == []


@pytest.mark.asyncio
async def test_schedule_passes_activation_floor_as_since(monkeypatch):
    now = datetime(2026, 6, 26, 14, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def fake_candidates(_db, _tenant, *, since, **_k):
        captured["since"] = since
        return []

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    await schedule_commercial_followups(DB_URL, TENANT, now=now)
    # cold-start: nothing older than now-48h ever queried
    assert captured["since"] >= now - timedelta(hours=48)


# ----------------------- per-round cap / dry-run -----------------------

@pytest.mark.asyncio
async def test_round_cap_limits_claim(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_schedule(*_a, **_k):
        return 0

    async def fake_claim(_db, _tenant, *, now, limit):
        captured["limit"] = limit
        return []

    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_CAP", raising=False)
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_DRY_RUN", raising=False)
    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)

    async def fake_wa(**_k):
        raise AssertionError("no due rows")

    await run_commercial_followup_job(DB_URL, TENANT, fake_wa, sleeper=_noop_sleep)
    assert captured["limit"] == mod.DEFAULT_ROUND_CAP  # 3, not the default 50


@pytest.mark.asyncio
async def test_dry_run_previews_without_sending(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_FOLLOWUP_DRY_RUN", "true")
    previewed: list[Any] = []

    async def fake_schedule(*_a, **_k):
        return 0

    async def fake_preview(_db, _tenant, *, now, limit):
        previewed.append(limit)
        return [{"id": "f1", "phone": "5511999990012", "stage": "checkout_incomplete", "contacts_sent": 0}]

    async def fake_claim(*_a, **_k):
        raise AssertionError("dry-run must NOT claim")

    async def fake_wa(**_k):
        raise AssertionError("dry-run must NOT send")

    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "preview_due_followups", fake_preview)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_wa, sleeper=_noop_sleep)
    assert result == 0
    assert previewed  # preview was consulted


# ----------------------- paid-order / segment guards in send loop -----------------------

@pytest.mark.asyncio
async def test_paid_order_cancels_send(monkeypatch):
    blocked: list[str] = []

    async def fake_schedule(*_a, **_k):
        return 0

    async def fake_claim(*_a, **_k):
        return [{"id": "f1", "phone": "5511999990013", "stage": "checkout_incomplete",
                 "contacts_sent": 0, "context_messages": "cpf cep"}]

    async def fake_block(_db, fid, reason):
        blocked.append(reason)

    async def fake_wa(**_k):
        raise AssertionError("paid lead must NOT be messaged")

    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_DRY_RUN", raising=False)
    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)
    monkeypatch.setattr(mod, "is_followup_opted_out", _async_false)
    monkeypatch.setattr(mod, "_has_paid_order", _async_true)
    monkeypatch.setattr(mod, "mark_followup_blocked", fake_block)

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_wa, sleeper=_noop_sleep)
    assert result == 0
    assert blocked == ["already_paid"]


# ----------------------- max touches cap (decision-6) -----------------------

@pytest.mark.asyncio
async def test_loop_blocks_lead_past_max_touches(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_MAX_TOUCHES", raising=False)
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_DRY_RUN", raising=False)
    blocked: list[str] = []

    async def fake_schedule(*_a, **_k):
        return 0

    async def fake_claim(*_a, **_k):
        return [{"id": "f9", "phone": "5511999990015", "stage": "checkout_incomplete",
                 "contacts_sent": 3, "context_messages": "cpf"}]

    async def fake_block(_db, fid, reason):
        blocked.append(reason)

    async def fake_wa(**_k):
        raise AssertionError("lead at max touches must NOT be messaged")

    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)
    monkeypatch.setattr(mod, "mark_followup_blocked", fake_block)

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_wa, sleeper=_noop_sleep)
    assert result == 0
    assert blocked == ["max_touches"]


@pytest.mark.asyncio
async def test_mark_sent_stops_after_third_touch(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FOLLOWUP_MAX_TOUCHES", raising=False)
    captured: dict[str, Any] = {}

    class FakeConn:
        async def execute(self, _query, *args):
            captured["args"] = args

        async def close(self):
            return None

    async def fake_connect(_url):
        return FakeConn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    plan = build_followup_plan(messages="cpf cep", stage=FollowupStage.CHECKOUT_INCOMPLETE, contacts_already_sent=2)
    contact = plan.contacts[0]
    row = {"id": "f9", "contacts_sent": 2, "context_messages": "cpf cep"}
    await mod.mark_followup_sent(DB_URL, row, plan=plan, contact=contact, sent_at=datetime.now(timezone.utc))

    # args: (followup_id, new_count, status, next_send_at, sent_at, template_id, temperature)
    _id, new_count, status, next_send_at = captured["args"][0], captured["args"][1], captured["args"][2], captured["args"][3]
    assert new_count == 3
    assert status == "completed"
    assert next_send_at is None  # no 4th touch ever


# ----------------------- message variation + personalization -----------------------

def test_message_varies_across_three_touches():
    texts = []
    for sent in range(3):
        plan = build_followup_plan(
            messages="cpf cep endereco",
            stage=FollowupStage.CHECKOUT_INCOMPLETE,
            contacts_already_sent=sent,
        )
        assert plan.allowed and plan.contacts
        texts.append(plan.contacts[0].text)
    assert len(set(texts)) == 3  # the 3 touches use distinct templates


def test_personalize_prefixes_first_name():
    assert mod._personalize("Tudo certo?", "Maria Silva") == "Oi Maria! Tudo certo?"
    assert mod._personalize("Tudo certo?", "") == "Tudo certo?"        # no name -> bare template
    assert mod._personalize("Tudo certo?", "12345") == "Tudo certo?"   # junk name rejected
    assert mod._personalize("Tudo certo?", "JOÃO") == "Oi João! Tudo certo?"


def test_personalize_never_echoes_symptom():
    # the encrypted symptom/pain must never reach the message; _personalize only
    # uses the plaintext first name, never the template gains symptom text.
    out = mod._personalize("Posso te ajudar a finalizar?", "Ana")
    assert "Ana" in out
    assert "insonia" not in out.lower() and "menopausa" not in out.lower()


# ----------------------- reply cancels the sequence (AC-6) -----------------------

@pytest.mark.asyncio
async def test_reply_marks_sequence_completed(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeConn:
        async def fetchrow(self, query, *_args):
            captured["query"] = query
            return {"stage": "checkout_incomplete", "last_temperature": "warm", "contacts_sent": 1}

        async def close(self):
            return None

    async def fake_connect(_url):
        return FakeConn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    monkeypatch.setattr(mod, "_emit_event", lambda *_a, **_k: None)

    ok = await mod.mark_followup_replied(DB_URL, TENANT, "5511999990014")
    assert ok is True
    # reply must stop pending sends
    assert "status = 'completed'" in captured["query"]
    assert "next_send_at = NULL" in captured["query"]


# helpers --------------------------------------------------------------

async def _async_false(*_a, **_k):
    return False


async def _async_true(*_a, **_k):
    return True
