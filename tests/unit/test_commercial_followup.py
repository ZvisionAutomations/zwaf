"""Unit tests for commercial follow-up engine (story-065).

Fakes asyncpg so the job logic runs entirely offline.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from zwaf.conversion.commercial_followup import (
    enroll_lead_for_followup,
    get_due_followups,
    mark_followup_replied,
    run_commercial_followup_job,
    update_followup_state,
)
from zwaf.conversion.followup import FollowupStage, LeadTemperature, build_followup_plan

TENANT = "livia-raiz-vital"
DB_URL = "postgresql://zwaf:test@postgres:5432/zwaf"


# ---------------------------------------------------------------------------
# Fake DB helpers
# ---------------------------------------------------------------------------


class FakeConn:
    def __init__(
        self,
        *,
        rows=None,
        fetchval_value=None,
        execute_result="INSERT 0 1",
        executed=None,
    ):
        self._rows = rows or []
        self._fetchval_value = fetchval_value
        self._execute_result = execute_result
        self.executed: list[str] = executed if executed is not None else []

    async def close(self):
        return None

    async def fetch(self, query: str, *args):
        return self._rows

    async def fetchval(self, query: str, *args):
        return self._fetchval_value

    async def execute(self, query: str, *args):
        self.executed.append(query.strip().split("\n")[0])
        return self._execute_result

    async def fetchrow(self, query: str, *args):
        return self._rows[0] if self._rows else None


# ---------------------------------------------------------------------------
# enroll_lead_for_followup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enroll_lead_returns_true_on_insert(monkeypatch):
    async def fake_connect(_url):
        return FakeConn(execute_result="INSERT 0 1")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    result = await enroll_lead_for_followup(
        DB_URL, TENANT, "5511999990001", "post_offer"
    )
    assert result is True


@pytest.mark.asyncio
async def test_enroll_lead_returns_false_on_conflict(monkeypatch):
    async def fake_connect(_url):
        return FakeConn(execute_result="INSERT 0 0")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    result = await enroll_lead_for_followup(
        DB_URL, TENANT, "5511999990001", "post_offer"
    )
    assert result is False


@pytest.mark.asyncio
async def test_enroll_lead_idempotent_no_exception(monkeypatch):
    """Calling enroll twice must not raise even when the second call returns conflict."""
    call_count = 0

    async def fake_connect(_url):
        nonlocal call_count
        result = "INSERT 0 1" if call_count == 0 else "INSERT 0 0"
        call_count += 1
        return FakeConn(execute_result=result)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    await enroll_lead_for_followup(DB_URL, TENANT, "5511999990001", "post_offer")
    await enroll_lead_for_followup(DB_URL, TENANT, "5511999990001", "post_offer")
    assert call_count == 2


@pytest.mark.asyncio
async def test_enroll_lead_empty_db_url():
    result = await enroll_lead_for_followup("", TENANT, "5511999990001", "post_offer")
    assert result is False


# ---------------------------------------------------------------------------
# get_due_followups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_due_followups_returns_rows(monkeypatch):
    row = {
        "id": "fu-1",
        "lead_phone": "5511999990001",
        "stage": "post_offer",
        "temperature": "warm",
        "contacts_sent": 0,
    }

    async def fake_connect(_url):
        return FakeConn(rows=[row])

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    result = await get_due_followups(DB_URL, TENANT)
    assert len(result) == 1
    assert result[0]["id"] == "fu-1"


@pytest.mark.asyncio
async def test_get_due_followups_empty_db_url():
    result = await get_due_followups("", TENANT)
    assert result == []


# ---------------------------------------------------------------------------
# run_commercial_followup_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_followup_job_sends_warm_lead(monkeypatch):
    """A warm lead in POST_OFFER stage should get a message sent."""
    import uuid

    followup_id = str(uuid.uuid4())
    due_row = {
        "id": followup_id,
        "lead_phone": "5511999990002",
        "stage": "post_offer",
        "temperature": "warm",
        "contacts_sent": 0,
    }

    connect_count = [0]

    async def fake_connect(_url):
        connect_count[0] += 1
        if connect_count[0] == 1:
            # get_due_followups
            return FakeConn(rows=[due_row])
        if connect_count[0] == 2:
            # optimistic lock
            return FakeConn(fetchval_value=followup_id)
        if connect_count[0] == 3:
            # is_opted_out
            return FakeConn(fetchval_value=False)
        # update_followup_state
        return FakeConn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    sent_messages: list[dict[str, Any]] = []

    async def fake_whatsapp(*, phone: str, message: str):
        sent_messages.append({"phone": phone, "message": message})

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 1
    assert len(sent_messages) == 1
    assert sent_messages[0]["phone"] == "5511999990002"
    assert len(sent_messages[0]["message"]) > 0


@pytest.mark.asyncio
async def test_run_followup_job_skips_opted_out(monkeypatch):
    """Opted-out leads must not receive messages."""
    import uuid

    followup_id = str(uuid.uuid4())
    due_row = {
        "id": followup_id,
        "lead_phone": "5511999990003",
        "stage": "post_offer",
        "temperature": "hot",
        "contacts_sent": 0,
    }

    connect_count = [0]

    async def fake_connect(_url):
        connect_count[0] += 1
        if connect_count[0] == 1:
            return FakeConn(rows=[due_row])
        if connect_count[0] == 2:
            # optimistic lock returns id (claimed)
            return FakeConn(fetchval_value=followup_id)
        if connect_count[0] == 3:
            # is_opted_out returns True
            return FakeConn(fetchval_value=True)
        return FakeConn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    async def fake_whatsapp(*, phone: str, message: str):
        raise AssertionError("should not send to opted-out lead")

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 0


@pytest.mark.asyncio
async def test_run_followup_job_empty_due_list(monkeypatch):
    """When there are no due followups, job returns 0 without calling whatsapp."""
    async def fake_connect(_url):
        return FakeConn(rows=[])

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    async def fake_whatsapp(*, phone: str, message: str):
        raise AssertionError("should not be called with empty due list")

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 0


@pytest.mark.asyncio
async def test_run_followup_job_noop_without_tool():
    result = await run_commercial_followup_job(DB_URL, TENANT, None)
    assert result == 0


@pytest.mark.asyncio
async def test_run_followup_job_skips_when_lock_not_claimed(monkeypatch):
    """If optimistic lock is not claimed (another worker got it), skip this row."""
    import uuid

    followup_id = str(uuid.uuid4())
    due_row = {
        "id": followup_id,
        "lead_phone": "5511999990004",
        "stage": "post_offer",
        "temperature": "warm",
        "contacts_sent": 0,
    }

    connect_count = [0]

    async def fake_connect(_url):
        connect_count[0] += 1
        if connect_count[0] == 1:
            return FakeConn(rows=[due_row])
        # lock returns None (not claimed)
        return FakeConn(fetchval_value=None)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    async def fake_whatsapp(*, phone: str, message: str):
        raise AssertionError("should not send when lock not claimed")

    result = await run_commercial_followup_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 0


# ---------------------------------------------------------------------------
# mark_followup_replied (story-065 CRITICAL-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_followup_replied_targets_pending(monkeypatch):
    """A re-engaged lead cancels only pending follow-ups, scoped by tenant+phone."""
    captured: dict[str, Any] = {}

    class Conn(FakeConn):
        async def execute(self, query: str, *args):
            captured["query"] = query
            captured["args"] = args
            return "UPDATE 1"

    async def fake_connect(_url):
        return Conn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    await mark_followup_replied(DB_URL, TENANT, "5511999990005")

    assert "status = 'replied'" in captured["query"]
    assert "status = 'pending'" in captured["query"]
    assert captured["args"] == (TENANT, "5511999990005")


@pytest.mark.asyncio
async def test_mark_followup_replied_empty_inputs_noop(monkeypatch):
    async def fake_connect(_url):
        raise AssertionError("must not open a connection without phone")

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    await mark_followup_replied(DB_URL, TENANT, "")  # no raise, no connect


# ---------------------------------------------------------------------------
# Send failure backoff (story-065 MEDIUM-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_followup_job_backoff_on_send_failure(monkeypatch):
    """A failed send reverts to pending WITH a future next_send_at (never NULL),
    so the due-query can pick it up again on the next hourly run."""
    import uuid

    followup_id = str(uuid.uuid4())
    due_row = {
        "id": followup_id,
        "lead_phone": "5511999990006",
        "stage": "post_offer",
        "temperature": "warm",
        "contacts_sent": 0,
    }

    update_calls: list[tuple] = []

    class UpdateConn(FakeConn):
        async def execute(self, query: str, *args):
            if "UPDATE commercial_followups" in query and "SET status = $2" in query:
                update_calls.append(args)  # (id, status, contacts_sent, next_send_at)
            return "UPDATE 1"

    connect_count = [0]

    async def fake_connect(_url):
        connect_count[0] += 1
        if connect_count[0] == 1:
            return FakeConn(rows=[due_row])
        if connect_count[0] == 2:
            return FakeConn(fetchval_value=followup_id)  # lock claimed
        if connect_count[0] == 3:
            return FakeConn(fetchval_value=False)  # not opted out
        return UpdateConn()  # update_followup_state after failure

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    async def failing_whatsapp(*, phone: str, message: str):
        raise RuntimeError("evolution api down")

    result = await run_commercial_followup_job(DB_URL, TENANT, failing_whatsapp)
    assert result == 0
    assert update_calls, "expected a state update on failure"
    last = update_calls[-1]
    assert last[1] == "pending"
    assert last[3] is not None  # backoff set — row retried, not orphaned


# ---------------------------------------------------------------------------
# temperature_override single-source-of-truth (story-065 HIGH-3/HIGH-4)
# ---------------------------------------------------------------------------


def test_build_followup_plan_temperature_override_no_synthetic_text():
    """The engine drives cadence by the persisted temperature, with NO message
    text — the plan honors the override and computes limits/delays correctly."""
    warm = build_followup_plan(
        messages=[],
        stage=FollowupStage.POST_OFFER,
        temperature_override="warm",
    )
    assert warm.allowed
    assert warm.temperature is LeadTemperature.WARM
    assert warm.max_contacts == 3
    assert warm.contacts[0].delay_hours == 1

    cold = build_followup_plan(
        messages=[],
        stage=FollowupStage.POST_OFFER,
        temperature_override="cold",
    )
    assert cold.max_contacts == 1

    # The following contact (already sent 1) uses the next delay slot (24h).
    warm_next = build_followup_plan(
        messages=[],
        stage=FollowupStage.POST_OFFER,
        contacts_already_sent=1,
        temperature_override="warm",
    )
    assert warm_next.contacts[0].delay_hours == 24