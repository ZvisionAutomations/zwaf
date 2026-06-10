"""Unit tests for lead memory persistence (story-044, F1).

A faithful in-memory fake stands in for asyncpg so the encrypted round-trip,
opt-out purge and retention sweep are exercised without a live Postgres. A real
Fernet key is configured so encrypt/decrypt is genuinely verified (the health
field must never be stored in plaintext).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from cryptography.fernet import Fernet

from zwaf.memory import lead_store


# ---------------------------------------------------------------------------
# In-memory fake connection
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        # (tenant_id, phone) -> column dict
        self.leads: dict[tuple[str, str], dict] = {}

    def row(self, tenant, phone):
        return self.leads.get((tenant, phone))


class FakeConn:
    """Simulates exactly the queries issued by lead_store memory functions."""

    def __init__(self, store: FakeStore):
        self.store = store

    async def close(self):
        return None

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "SELECT name, primary_symptom_enc" in q:
            tenant, phone = args
            row = self.store.row(tenant, phone)
            return dict(row) if row else None
        raise AssertionError(f"unexpected fetchrow: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())

        # mark_opt_out — first statement (lead_profiles), modelled as no-op
        if "INSERT INTO lead_profiles" in q:
            return "INSERT 0 1"

        # upsert_lead_memory — INSERT ... ON CONFLICT DO UPDATE (COALESCE preserve)
        if "INSERT INTO leads" in q and "primary_symptom_enc" in q and "ON CONFLICT" in q:
            tenant, phone, symptom_enc, summary_enc, objections_json, nba = args
            key = (tenant, phone)
            existing = self.store.leads.get(key)
            now = datetime.now(timezone.utc)
            if existing is None:
                self.store.leads[key] = {
                    "name": None,
                    "primary_symptom_enc": symptom_enc,
                    "memory_summary_enc": summary_enc,
                    "objections": objections_json if objections_json is not None else "[]",
                    "next_best_action": nba,
                    "memory_updated_at": now,
                    "memory_purged_at": None,
                }
            else:
                if symptom_enc is not None:
                    existing["primary_symptom_enc"] = symptom_enc
                if summary_enc is not None:
                    existing["memory_summary_enc"] = summary_enc
                if objections_json is not None:
                    existing["objections"] = objections_json
                if nba is not None:
                    existing["next_best_action"] = nba
                existing["memory_updated_at"] = now
            return "INSERT 0 1"

        # mark_opt_out — UPDATE leads (opt-out + purge memory)
        if "UPDATE leads" in q and "opt_out_at" in q:
            tenant, phone, _reason = args
            row = self.store.row(tenant, phone)
            if not row:
                return "UPDATE 0"
            row["primary_symptom_enc"] = None
            row["memory_summary_enc"] = None
            row["objections"] = "[]"
            row["next_best_action"] = None
            row["memory_purged_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"

        # purge_expired_memory — UPDATE leads ... memory_updated_at < $2
        if "UPDATE leads" in q and "memory_updated_at < $2" in q:
            tenant, cutoff = args
            n = 0
            for (t, _p), row in self.store.leads.items():
                if t != tenant:
                    continue
                mu = row.get("memory_updated_at")
                has_memory = (
                    row.get("primary_symptom_enc") is not None
                    or row.get("memory_summary_enc") is not None
                    or row.get("next_best_action") is not None
                    or (row.get("objections") not in (None, "[]"))
                )
                if mu is not None and mu < cutoff and has_memory:
                    row["primary_symptom_enc"] = None
                    row["memory_summary_enc"] = None
                    row["objections"] = "[]"
                    row["next_best_action"] = None
                    row["memory_purged_at"] = datetime.now(timezone.utc)
                    n += 1
            return f"UPDATE {n}"

        raise AssertionError(f"unexpected execute: {q}")


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()

    async def fake_connect(_db_url):
        return FakeConn(s)

    monkeypatch.setenv("DATABASE_URL", "postgresql://zwaf:test@postgres:5432/zwaf")
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    # Ensure module-level override does not shadow the env URL.
    lead_store.configure("")
    return s


@pytest.fixture
def with_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ZWAF_PII_FERNET_KEY", key)
    return key


TENANT = "livia-raiz-vital"
PHONE = "5511999990001"


# ---------------------------------------------------------------------------
# Encrypted round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_roundtrip_encrypted(store, with_key):
    await lead_store.upsert_lead_memory(
        PHONE, TENANT,
        primary_symptom="insônia e calor há 2 anos",
        objections=["achou caro"],
        memory_summary="Cliente no climatério, sono ruim; ficou de pensar no valor.",
        next_best_action="retomar 3 potes, tratar preço pela faixa de R$128",
    )

    # Stored health fields must NOT be plaintext.
    raw = store.row(TENANT, PHONE)
    assert raw["primary_symptom_enc"] != "insônia e calor há 2 anos"
    assert "insônia" not in (raw["primary_symptom_enc"] or "")
    assert "climatério" not in (raw["memory_summary_enc"] or "")

    mem = await lead_store.get_lead_memory(PHONE, TENANT)
    assert mem is not None
    assert mem["primary_symptom"] == "insônia e calor há 2 anos"
    assert mem["memory_summary"].startswith("Cliente no climatério")
    assert mem["objections"] == ["achou caro"]
    assert mem["next_best_action"].startswith("retomar 3 potes")


@pytest.mark.asyncio
async def test_get_lead_memory_none_when_absent(store, with_key):
    assert await lead_store.get_lead_memory("5511000000000", TENANT) is None


@pytest.mark.asyncio
async def test_coalesce_preserves_unset_fields(store, with_key):
    # First write only the symptom.
    await lead_store.upsert_lead_memory(PHONE, TENANT, primary_symptom="calor")
    # Then write only objections — symptom must survive.
    await lead_store.upsert_lead_memory(PHONE, TENANT, objections=["vou pensar"])

    mem = await lead_store.get_lead_memory(PHONE, TENANT)
    assert mem["primary_symptom"] == "calor"
    assert mem["objections"] == ["vou pensar"]


# ---------------------------------------------------------------------------
# Graceful degradation (no encryption key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation_without_key(store, monkeypatch):
    monkeypatch.delenv("ZWAF_PII_FERNET_KEY", raising=False)
    monkeypatch.delenv("DOCUMENT_ENCRYPTION_KEY", raising=False)

    await lead_store.upsert_lead_memory(
        PHONE, TENANT,
        primary_symptom="insônia",
        objections=["achou caro"],
        next_best_action="retomar",
    )

    raw = store.row(TENANT, PHONE)
    # Health field NOT stored without a key (never plaintext).
    assert raw["primary_symptom_enc"] is None
    # Commercial fields still stored.
    assert raw["next_best_action"] == "retomar"

    mem = await lead_store.get_lead_memory(PHONE, TENANT)
    assert mem["primary_symptom"] == ""
    assert mem["objections"] == ["achou caro"]


# ---------------------------------------------------------------------------
# Opt-out purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_opt_out_purges_memory(store, with_key):
    await lead_store.upsert_lead_memory(
        PHONE, TENANT, primary_symptom="insônia", memory_summary="resumo",
    )
    await lead_store.mark_opt_out(phone=PHONE, tenant_id=TENANT)

    raw = store.row(TENANT, PHONE)
    assert raw["primary_symptom_enc"] is None
    assert raw["memory_summary_enc"] is None
    assert raw["objections"] == "[]"
    assert raw["memory_purged_at"] is not None

    mem = await lead_store.get_lead_memory(PHONE, TENANT)
    assert mem["primary_symptom"] == ""
    assert mem["memory_summary"] == ""


# ---------------------------------------------------------------------------
# Retention sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_expired_memory_idempotent(store, with_key):
    await lead_store.upsert_lead_memory(PHONE, TENANT, primary_symptom="calor")
    # Age the memory past the retention window.
    store.row(TENANT, PHONE)["memory_updated_at"] = datetime.now(timezone.utc) - timedelta(days=800)

    first = await lead_store.purge_expired_memory(TENANT, retention_months=24)
    second = await lead_store.purge_expired_memory(TENANT, retention_months=24)

    assert first == 1
    assert second == 0  # idempotent — already purged
    assert store.row(TENANT, PHONE)["primary_symptom_enc"] is None


@pytest.mark.asyncio
async def test_purge_expired_keeps_fresh_memory(store, with_key):
    await lead_store.upsert_lead_memory(PHONE, TENANT, primary_symptom="calor")
    # Fresh memory (updated just now) must survive the sweep.
    purged = await lead_store.purge_expired_memory(TENANT, retention_months=24)

    assert purged == 0
    mem = await lead_store.get_lead_memory(PHONE, TENANT)
    assert mem["primary_symptom"] == "calor"
