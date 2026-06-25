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
    run_commercial_followup_job,
    update_followup_state,
)

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