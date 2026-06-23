"""Unit tests for PIX re-engagement job (story-051).

Fakes asyncpg so the job logic runs entirely offline.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import asyncpg
import pytest

from zwaf.conversion.pix_reengagement import (
    build_reengagement_message,
    get_pending_pix_orders,
    is_opted_out,
    mark_reengagement_sent,
    run_pix_reengagement_job,
)

TENANT = "livia-raiz-vital"
DB_URL = "postgresql://zwaf:test@postgres:5432/zwaf"
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class FakeConn:
    def __init__(self, *, rows=None, fetchval_value=None, executed=None):
        self._rows = rows or []
        self._fetchval_value = fetchval_value
        self.executed: list[str] = executed if executed is not None else []

    async def close(self):
        return None

    async def fetch(self, query: str, *args):
        return self._rows

    async def fetchval(self, query: str, *args):
        return self._fetchval_value

    async def execute(self, query: str, *args):
        self.executed.append(query.strip().split("\n")[0])
        return "UPDATE 1"


# ---------------------------------------------------------------------------
# get_pending_pix_orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pending_pix_orders_returns_rows(monkeypatch):
    row = {
        "id": "order-1",
        "lead_phone": "5511999990001",
        "total_cents": 14900,
        "pix_due_date": TOMORROW,
        "asaas_payment_url": "https://asaas.com/pay/abc",
    }

    async def fake_connect(_url):
        # asyncpg Records support dict() — use plain dicts as stand-ins
        return FakeConn(rows=[row])

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    result = await get_pending_pix_orders(DB_URL, TENANT)
    assert len(result) == 1
    assert result[0]["id"] == "order-1"


@pytest.mark.asyncio
async def test_get_pending_pix_orders_empty_db_url():
    result = await get_pending_pix_orders("", TENANT)
    assert result == []


# ---------------------------------------------------------------------------
# is_opted_out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_opted_out_true(monkeypatch):
    async def fake_connect(_url):
        return FakeConn(fetchval_value=True)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    assert await is_opted_out(DB_URL, TENANT, "5511999990001") is True


@pytest.mark.asyncio
async def test_is_opted_out_false(monkeypatch):
    async def fake_connect(_url):
        return FakeConn(fetchval_value=False)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    assert await is_opted_out(DB_URL, TENANT, "5511999990001") is False


@pytest.mark.asyncio
async def test_is_opted_out_empty_phone():
    result = await is_opted_out(DB_URL, TENANT, "")
    assert result is False


# ---------------------------------------------------------------------------
# mark_reengagement_sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_reengagement_sent_executes_update(monkeypatch):
    conn = FakeConn()

    async def fake_connect(_url):
        return conn

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    await mark_reengagement_sent(DB_URL, "order-1")
    assert len(conn.executed) == 1
    assert "UPDATE" in conn.executed[0]


@pytest.mark.asyncio
async def test_mark_reengagement_sent_noop_on_empty():
    await mark_reengagement_sent("", "order-1")  # no exception


# ---------------------------------------------------------------------------
# build_reengagement_message
# ---------------------------------------------------------------------------


def test_build_reengagement_message_contains_price():
    msg = build_reengagement_message(total_cents=14900, pix_due_date=TOMORROW)
    assert "R$ 149,00" in msg
    assert "Lívia" in msg
    assert "assistente virtual" in msg


def test_build_reengagement_message_no_due_date():
    msg = build_reengagement_message(total_cents=29800, pix_due_date=None)
    assert "hoje" in msg
    assert "R$ 298,00" in msg


def test_build_reengagement_message_without_memory_matches_legacy():
    legacy = build_reengagement_message(total_cents=14900, pix_due_date=TOMORROW)
    fallback = build_reengagement_message(
        total_cents=14900,
        pix_due_date=TOMORROW,
        lead_memory={},
    )
    assert fallback == legacy


def test_build_reengagement_message_with_price_objection_memory():
    msg = build_reengagement_message(
        total_cents=14900,
        pix_due_date=TOMORROW,
        lead_memory={
            "primary_symptom": "sono ruim e calores",
            "objections": ["preco"],
        },
    )
    assert "sono ruim e calores" in msg
    assert "R$ 4,96" in msg
    assert "cura" not in msg.lower()
    assert "garantia" not in msg.lower()


# ---------------------------------------------------------------------------
# run_pix_reengagement_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pix_reengagement_job_sends_and_stamps(monkeypatch):
    order_row = {
        "id": "order-2",
        "lead_phone": "5511999990002",
        "total_cents": 14900,
        "pix_due_date": TOMORROW,
        "asaas_payment_url": "https://asaas.com/pay/xyz",
    }
    executed: list[str] = []

    async def fake_connect(_url):
        return FakeConn(rows=[order_row], fetchval_value=False, executed=executed)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    sent_messages: list[dict[str, Any]] = []

    async def fake_whatsapp(*, phone: str, message: str):
        sent_messages.append({"phone": phone, "message": message})

    result = await run_pix_reengagement_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 1
    assert len(sent_messages) == 1
    assert sent_messages[0]["phone"] == "5511999990002"


@pytest.mark.asyncio
async def test_run_pix_reengagement_job_skips_opted_out(monkeypatch):
    order_row = {
        "id": "order-3",
        "lead_phone": "5511999990003",
        "total_cents": 14900,
        "pix_due_date": TOMORROW,
        "asaas_payment_url": None,
    }

    async def fake_connect(_url):
        return FakeConn(rows=[order_row], fetchval_value=True)

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    async def fake_whatsapp(*, phone: str, message: str):
        raise AssertionError("should not send to opted-out lead")

    result = await run_pix_reengagement_job(DB_URL, TENANT, fake_whatsapp)
    assert result == 0


@pytest.mark.asyncio
async def test_run_pix_reengagement_job_noop_without_tool():
    result = await run_pix_reengagement_job(DB_URL, TENANT, None)
    assert result == 0


@pytest.mark.asyncio
async def test_run_pix_reengagement_job_passes_memory_to_message(monkeypatch):
    import zwaf.conversion.pix_reengagement as mod

    order_row = {
        "id": "order-4",
        "lead_phone": "5511999990004",
        "total_cents": 14900,
        "pix_due_date": TOMORROW,
        "asaas_payment_url": None,
    }
    sent_messages: list[dict[str, Any]] = []

    async def fake_orders(*_args, **_kwargs):
        return [order_row]

    async def fake_optout(*_args, **_kwargs):
        return False

    async def fake_memory(*_args, **_kwargs):
        return {"primary_symptom": "calores", "objections": ["preco"]}

    async def fake_mark(*_args, **_kwargs):
        return None

    async def fake_whatsapp(*, phone: str, message: str):
        sent_messages.append({"phone": phone, "message": message})

    monkeypatch.setattr(mod, "get_pending_pix_orders", fake_orders)
    monkeypatch.setattr(mod, "is_opted_out", fake_optout)
    monkeypatch.setattr(mod, "get_lead_reengagement_memory", fake_memory)
    monkeypatch.setattr(mod, "mark_reengagement_sent", fake_mark)

    result = await run_pix_reengagement_job(DB_URL, TENANT, fake_whatsapp)

    assert result == 1
    assert "calores" in sent_messages[0]["message"]


@pytest.mark.asyncio
async def test_pix_reengagement_supports_whatsapp_tool_object(monkeypatch):
    import zwaf.conversion.pix_reengagement as mod

    class FakeWhatsAppTool:
        def __init__(self):
            self.sent: list[dict[str, str]] = []

        async def send_message(self, *, phone: str, text: str, session_id: str | None = None):
            self.sent.append({"phone": phone, "text": text, "session_id": session_id or ""})

    order_row = {
        "id": "order-5",
        "lead_phone": "5511999990005",
        "total_cents": 14900,
        "pix_due_date": TOMORROW,
        "asaas_payment_url": None,
    }
    tool = FakeWhatsAppTool()

    async def fake_orders(*_args, **_kwargs):
        return [order_row]

    async def fake_optout(*_args, **_kwargs):
        return False

    async def fake_memory(*_args, **_kwargs):
        return None

    async def fake_mark(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mod, "get_pending_pix_orders", fake_orders)
    monkeypatch.setattr(mod, "is_opted_out", fake_optout)
    monkeypatch.setattr(mod, "get_lead_reengagement_memory", fake_memory)
    monkeypatch.setattr(mod, "mark_reengagement_sent", fake_mark)

    assert await run_pix_reengagement_job(DB_URL, TENANT, tool) == 1
    assert tool.sent[0]["phone"] == "5511999990005"
