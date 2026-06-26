"""Unit tests for commercial follow-up engine (story-065)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from zwaf.conversion import commercial_followup as mod
from zwaf.conversion.followup import FollowupStage
from zwaf.conversion.commercial_followup import (
    FollowupCandidate,
    mark_followup_replied,
    run_commercial_followup_job,
    schedule_commercial_followups,
)

TENANT = "livia-raiz-vital"
DB_URL = "postgresql://zwaf:test@postgres:5432/zwaf"


@pytest.mark.asyncio
async def test_schedule_followups_creates_due_warm_post_offer(monkeypatch):
    now = datetime(2026, 6, 22, 13, 0, tzinfo=timezone.utc)
    candidate = FollowupCandidate(
        phone="5511999990001",
        stage=FollowupStage.POST_OFFER,
        messages="qual o valor preco",
        last_activity_at=now,
    )
    scheduled: list[dict[str, Any]] = []

    async def fake_candidates(*_args, **_kwargs):
        return [candidate]

    async def fake_optout(*_args, **_kwargs):
        return False

    async def fake_upsert(_db_url, tenant_id, candidate_arg, *, plan, next_send_at):
        scheduled.append(
            {
                "tenant_id": tenant_id,
                "candidate": candidate_arg,
                "plan": plan,
                "next_send_at": next_send_at,
            }
        )

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    monkeypatch.setattr(mod, "is_followup_opted_out", fake_optout)
    monkeypatch.setattr(mod, "upsert_scheduled_followup", fake_upsert)
    monkeypatch.setattr(mod, "_emit_event", lambda *_args, **_kwargs: None)

    result = await schedule_commercial_followups(DB_URL, TENANT, now=now)

    assert result == 1
    assert scheduled[0]["plan"].temperature.value == "warm"
    assert scheduled[0]["plan"].contacts[0].delay_hours == 1
    assert scheduled[0]["next_send_at"] == now.replace(hour=14)


@pytest.mark.asyncio
async def test_run_job_sends_due_followup_and_persists_counter(monkeypatch):
    sent_messages: list[dict[str, str]] = []
    persisted: list[dict[str, Any]] = []

    async def fake_schedule(*_args, **_kwargs):
        return 1

    async def fake_claim(*_args, **_kwargs):
        return [
            {
                "id": "f1",
                "phone": "5511999990002",
                "stage": "post_offer",
                "contacts_sent": 0,
                "context_messages": "qual o valor preco",
            }
        ]

    async def fake_optout(*_args, **_kwargs):
        return False

    async def fake_whatsapp(*, phone: str, message: str):
        sent_messages.append({"phone": phone, "message": message})

    async def fake_mark_sent(_db_url, row, *, plan, contact, sent_at):
        persisted.append({"row": row, "plan": plan, "contact": contact, "sent_at": sent_at})

    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)
    monkeypatch.setattr(mod, "is_followup_opted_out", fake_optout)
    monkeypatch.setattr(mod, "mark_followup_sent", fake_mark_sent)
    monkeypatch.setattr(mod, "_emit_event", lambda *_args, **_kwargs: None)

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp)

    assert result == 1
    assert sent_messages[0]["phone"] == "5511999990002"
    assert "duvida" in sent_messages[0]["message"].lower()
    assert persisted[0]["contact"].sequence == 1


@pytest.mark.asyncio
async def test_run_job_does_not_resend_sending_row_after_restart(monkeypatch):
    async def fake_schedule(*_args, **_kwargs):
        return 0

    async def fake_claim(*_args, **_kwargs):
        return []

    async def fake_whatsapp(*, phone: str, message: str):
        raise AssertionError("should not send when no due rows are claimed")

    monkeypatch.setattr(mod, "schedule_commercial_followups", fake_schedule)
    monkeypatch.setattr(mod, "claim_due_followups", fake_claim)

    assert await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp) == 0


@pytest.mark.asyncio
async def test_opt_out_blocks_schedule(monkeypatch):
    candidate = FollowupCandidate(
        phone="5511999990003",
        stage=FollowupStage.POST_OFFER,
        messages="qual o valor preco",
        last_activity_at=datetime.now(timezone.utc),
    )
    blocked: list[str] = []

    async def fake_candidates(*_args, **_kwargs):
        return [candidate]

    async def fake_optout(*_args, **_kwargs):
        return True

    async def fake_blocked(_db_url, _tenant_id, _candidate, reason):
        blocked.append(reason)

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    monkeypatch.setattr(mod, "is_followup_opted_out", fake_optout)
    monkeypatch.setattr(mod, "upsert_blocked_followup", fake_blocked)

    assert await schedule_commercial_followups(DB_URL, TENANT) == 0
    assert blocked == ["opt_out"]


@pytest.mark.asyncio
async def test_medical_risk_blocks_schedule(monkeypatch):
    candidate = FollowupCandidate(
        phone="5511999990004",
        stage=FollowupStage.POST_OFFER,
        messages="tomo remedio tive reacao",
        last_activity_at=datetime.now(timezone.utc),
    )
    blocked: list[str] = []

    async def fake_candidates(*_args, **_kwargs):
        return [candidate]

    async def fake_optout(*_args, **_kwargs):
        return False

    async def fake_blocked(_db_url, _tenant_id, _candidate, reason):
        blocked.append(reason)

    monkeypatch.setattr(mod, "get_followup_candidates", fake_candidates)
    monkeypatch.setattr(mod, "is_followup_opted_out", fake_optout)
    monkeypatch.setattr(mod, "upsert_blocked_followup", fake_blocked)

    assert await schedule_commercial_followups(DB_URL, TENANT) == 0
    assert blocked == ["medical_risk"]


def test_business_hours_rolls_to_next_window():
    due = datetime(2026, 6, 22, 22, 30, tzinfo=timezone.utc)
    adjusted = mod._next_business_time(due)
    assert adjusted.astimezone(mod.BRT).hour == 8
    assert adjusted.astimezone(mod.BRT).day == 23


@pytest.mark.asyncio
async def test_mark_followup_replied_marks_once(monkeypatch):
    class FakeConn:
        async def fetchrow(self, _query, *_args):
            return {"stage": "post_offer", "last_temperature": "warm", "contacts_sent": 1}

        async def close(self):
            return None

    async def fake_connect(_url):
        return FakeConn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    monkeypatch.setattr(mod, "_emit_event", lambda *_args, **_kwargs: None)

    assert await mark_followup_replied(DB_URL, TENANT, "5511999990005") is True


def test_normalize_dsn_strips_asyncpg_dialect():
    # story-081: asyncpg rejeita o dialeto SQLAlchemy postgresql+asyncpg://
    assert (
        mod._normalize_dsn("postgresql+asyncpg://zwaf:test@postgres:5432/zwaf")
        == "postgresql://zwaf:test@postgres:5432/zwaf"
    )
    # idempotente para DSN ja limpa
    assert (
        mod._normalize_dsn("postgresql://zwaf:test@postgres:5432/zwaf")
        == "postgresql://zwaf:test@postgres:5432/zwaf"
    )
    assert mod._normalize_dsn("") == ""


@pytest.mark.asyncio
async def test_mark_followup_replied_normalizes_dsn_before_connect(monkeypatch):
    # story-081: a DSN no formato SQLAlchemy deve chegar normalizada ao asyncpg.connect
    captured: dict[str, Any] = {}

    class FakeConn:
        async def fetchrow(self, _query, *_args):
            return {"stage": "post_offer", "last_temperature": "warm", "contacts_sent": 1}

        async def close(self):
            return None

    async def fake_connect(url):
        captured["url"] = url
        return FakeConn()

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    monkeypatch.setattr(mod, "_emit_event", lambda *_args, **_kwargs: None)

    sqlalchemy_dsn = "postgresql+asyncpg://zwaf:test@postgres:5432/zwaf"
    await mark_followup_replied(sqlalchemy_dsn, TENANT, "5511999990006")

    assert "+asyncpg" not in captured["url"]
    assert captured["url"] == "postgresql://zwaf:test@postgres:5432/zwaf"
