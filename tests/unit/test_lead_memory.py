"""Unit tests for the lead memory block builder + reinjection (story-044, F2).

Fakes asyncpg so the durable signals (paid / open payment / conversion) and the
decrypted semantic memory are exercised offline. A real Fernet key makes the
symptom round-trip genuine.
"""
from __future__ import annotations

from types import SimpleNamespace

import asyncpg
import pytest
from cryptography.fernet import Fernet

from zwaf.core import base_agent
from zwaf.memory import lead_memory, lead_store
from zwaf.security import pii


TENANT = "livia-raiz-vital"
PHONE = "5511999990001"


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.lead: dict | None = None      # leads row (with *_enc columns) or None
        self.paid: int = 0                  # count of PAID payment_events
        self.open_payment: dict | None = None
        self.last_signal: dict | None = None


class FakeConn:
    def __init__(self, store: FakeStore):
        self.store = store

    async def close(self):
        return None

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if "COUNT(*) FROM payment_events" in q:
            return self.store.paid
        raise AssertionError(f"unexpected fetchval: {q}")

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "SELECT name, primary_symptom_enc" in q:
            return self.store.lead
        if "FROM payment_events" in q and "PENDING" in q:
            return self.store.open_payment
        if "FROM conversion_events" in q:
            return self.store.last_signal
        raise AssertionError(f"unexpected fetchrow: {q}")


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()

    async def fake_connect(_db_url):
        return FakeConn(s)

    monkeypatch.setenv("DATABASE_URL", "postgresql://zwaf:test@postgres:5432/zwaf")
    monkeypatch.setenv("ZWAF_PII_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    lead_store.configure("")
    return s


def _lead_row(**over):
    base = {
        "name": None,
        "primary_symptom_enc": None,
        "memory_summary_enc": None,
        "objections": "[]",
        "next_best_action": None,
        "memory_updated_at": None,
        "memory_purged_at": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_empty_for_new_lead(store):
    # No memory, never bought, no open payment -> nothing reinjected.
    block = await lead_memory.build_memory_block(PHONE, TENANT)
    assert block == ""


@pytest.mark.asyncio
async def test_block_for_returning_buyer(store):
    store.paid = 2
    block = await lead_memory.build_memory_block(PHONE, TENANT)
    assert "Memória deste lead" in block
    assert "Já é cliente" in block


@pytest.mark.asyncio
async def test_block_with_open_payment_prompts_recovery(store):
    store.open_payment = {
        "product_id": "new-woman", "amount_cents": 38400,
        "status": "PENDING", "created_at": None,
    }
    block = await lead_memory.build_memory_block(PHONE, TENANT)
    assert "Pedido em aberto" in block


# ---------------------------------------------------------------------------
# Semantic content (decrypted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_includes_name_symptom_and_objection(store):
    store.lead = _lead_row(
        name="Maria",
        primary_symptom_enc=pii.encrypt_pii("insônia e calor"),
        objections='["achou caro"]',
    )
    block = await lead_memory.build_memory_block(PHONE, TENANT)
    assert "Maria" in block
    assert "insônia e calor" in block          # decrypted into the block
    assert "achou caro" in block
    # The block must instruct caring/correctable use, not dossier recital.
    assert "como cuidado" in block
    assert "pergunta que permite correção" in block


@pytest.mark.asyncio
async def test_block_recency_from_session_state(store):
    store.paid = 1
    block = await lead_memory.build_memory_block(
        PHONE, TENANT, session_state={"last_quantity": 3, "last_billing_type": "PIX"}
    )
    assert "3 pote(s)" in block
    assert "Pix" in block


@pytest.mark.asyncio
async def test_block_truncated_to_max_chars(store):
    store.lead = _lead_row(
        name="Maria",
        memory_summary_enc=pii.encrypt_pii("x" * 5000),
    )
    block = await lead_memory.build_memory_block(PHONE, TENANT, max_chars=300)
    assert len(block) <= 302  # 300 + " …"


# ---------------------------------------------------------------------------
# Reinjection into the agent prompt
# ---------------------------------------------------------------------------


def test_build_agent_appends_memory_block(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    tenant = SimpleNamespace(
        tenant_id="test-tenant",
        agent_name="Lívia",
        llm=SimpleNamespace(primary="gpt-4o", temperature=0.4),
    )
    block = "## Memória deste lead\n- Nome: Maria"

    agent = base_agent.build_agent(
        agent_name="vendedor",
        tenant_config=tenant,
        tools=[],
        session_id="s1",
        lead_id="l1",
        db_url="",
        lead_memory_block=block,
    )
    assert "Memória deste lead" in agent.instructions
    assert "Maria" in agent.instructions


def test_build_agent_without_block_is_unchanged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    tenant = SimpleNamespace(
        tenant_id="test-tenant",
        agent_name="Lívia",
        llm=SimpleNamespace(primary="gpt-4o", temperature=0.4),
    )
    agent = base_agent.build_agent(
        agent_name="vendedor",
        tenant_config=tenant,
        tools=[],
        session_id="s1",
        lead_id="l1",
        db_url="",
    )
    assert "Memória deste lead" not in agent.instructions


# ---------------------------------------------------------------------------
# F3 — summarizer parser
# ---------------------------------------------------------------------------


def test_parse_summary_plain_json():
    raw = '{"primary_symptom": "calor", "objections": ["achou caro"], "memory_summary": "x", "next_best_action": "y"}'
    out = lead_memory._parse_summary(raw)
    assert out["primary_symptom"] == "calor"
    assert out["objections"] == ["achou caro"]


def test_parse_summary_code_fenced():
    raw = '```json\n{"primary_symptom": "insônia", "objections": [], "memory_summary": "", "next_best_action": ""}\n```'
    out = lead_memory._parse_summary(raw)
    assert out is not None
    assert out["primary_symptom"] == "insônia"
    assert out["objections"] == []


def test_parse_summary_garbage_returns_none():
    assert lead_memory._parse_summary("desculpe, não consegui") is None
    assert lead_memory._parse_summary("") is None


def test_parse_summary_sanitizes_and_truncates():
    raw = '{"primary_symptom": "' + "a" * 500 + '", "objections": "naoeh-lista", "memory_summary": 1, "next_best_action": null}'
    out = lead_memory._parse_summary(raw)
    assert len(out["primary_symptom"]) == 300        # truncated
    assert out["objections"] == []                   # non-list coerced to []
    assert out["next_best_action"] == ""             # null -> ""


# ---------------------------------------------------------------------------
# F3 — maybe_update_lead_memory (throttle + flow)
# ---------------------------------------------------------------------------


def _tenant(enabled=True, throttle=3):
    return SimpleNamespace(
        tenant_id=TENANT,
        lead_memory={"enabled": enabled, "throttle_turns": throttle, "summarizer_model": "gemini-1.5-flash"},
        llm=SimpleNamespace(primary="gpt-4o", temperature=0.4),
    )


@pytest.mark.asyncio
async def test_maybe_update_flag_off_noop(monkeypatch):
    calls = {"bump": 0}

    async def fake_bump(*a, **k):
        calls["bump"] += 1
        return 99

    monkeypatch.setattr(lead_memory, "bump_summary_counter", fake_bump)
    out = await lead_memory.maybe_update_lead_memory(
        phone=PHONE, tenant_id=TENANT, session_id="s", agent_name="vendedor",
        tenant_config=_tenant(enabled=False), db_url="",
    )
    assert out is False
    assert calls["bump"] == 0  # short-circuits before touching Redis


@pytest.mark.asyncio
async def test_maybe_update_below_throttle_noop(monkeypatch):
    read_called = {"n": 0}

    async def fake_bump(*a, **k):
        return 2  # below throttle=3

    async def fake_read(*a, **k):
        read_called["n"] += 1
        return "Cliente: x"

    monkeypatch.setattr(lead_memory, "bump_summary_counter", fake_bump)
    monkeypatch.setattr(lead_memory, "_read_chat_history", fake_read)
    out = await lead_memory.maybe_update_lead_memory(
        phone=PHONE, tenant_id=TENANT, session_id="s", agent_name="vendedor",
        tenant_config=_tenant(throttle=3), db_url="",
    )
    assert out is False
    assert read_called["n"] == 0  # no transcript read, no LLM


@pytest.mark.asyncio
async def test_maybe_update_summarizes_and_upserts(monkeypatch):
    captured = {}

    async def fake_bump(*a, **k):
        return 3  # hits throttle

    async def fake_reset(*a, **k):
        return None

    async def fake_read(*a, **k):
        return "Cliente: tenho calor e insônia\nLívia: entendo, vou te ajudar"

    async def fake_summarize(transcript, **k):
        return {
            "primary_symptom": "calor e insônia",
            "objections": ["achou caro"],
            "memory_summary": "Cliente no climatério.",
            "next_best_action": "retomar 3 potes",
        }

    async def fake_upsert(phone, tenant_id, **kwargs):
        captured.update(kwargs)
        captured["phone"] = phone

    monkeypatch.setattr(lead_memory, "bump_summary_counter", fake_bump)
    monkeypatch.setattr(lead_memory, "reset_summary_counter", fake_reset)
    monkeypatch.setattr(lead_memory, "_read_chat_history", fake_read)
    monkeypatch.setattr(lead_memory, "_summarize", fake_summarize)
    monkeypatch.setattr(lead_memory, "upsert_lead_memory", fake_upsert)

    out = await lead_memory.maybe_update_lead_memory(
        phone=PHONE, tenant_id=TENANT, session_id="s", agent_name="vendedor",
        tenant_config=_tenant(throttle=3), db_url="",
    )
    assert out is True
    assert captured["primary_symptom"] == "calor e insônia"
    assert captured["objections"] == ["achou caro"]
    assert captured["phone"] == PHONE


@pytest.mark.asyncio
async def test_maybe_update_empty_extraction_does_not_upsert(monkeypatch):
    upserted = {"n": 0}

    async def fake_bump(*a, **k):
        return 3

    async def fake_reset(*a, **k):
        return None

    async def fake_read(*a, **k):
        return "Cliente: oi"

    async def fake_summarize(transcript, **k):
        return {"primary_symptom": "", "objections": [], "memory_summary": "", "next_best_action": ""}

    async def fake_upsert(*a, **k):
        upserted["n"] += 1

    monkeypatch.setattr(lead_memory, "bump_summary_counter", fake_bump)
    monkeypatch.setattr(lead_memory, "reset_summary_counter", fake_reset)
    monkeypatch.setattr(lead_memory, "_read_chat_history", fake_read)
    monkeypatch.setattr(lead_memory, "_summarize", fake_summarize)
    monkeypatch.setattr(lead_memory, "upsert_lead_memory", fake_upsert)

    out = await lead_memory.maybe_update_lead_memory(
        phone=PHONE, tenant_id=TENANT, session_id="s", agent_name="vendedor",
        tenant_config=_tenant(throttle=3), db_url="",
    )
    assert out is False
    assert upserted["n"] == 0  # empty extraction must not wipe existing memory
