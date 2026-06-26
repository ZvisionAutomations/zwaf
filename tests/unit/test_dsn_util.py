"""Unit tests for the centralized DSN util (story-082)."""
from __future__ import annotations

import pytest

from zwaf.db import dsn


def test_normalize_dsn_strips_sqlalchemy_dialect():
    assert (
        dsn.normalize_dsn("postgresql+asyncpg://u:p@host:5432/db")
        == "postgresql://u:p@host:5432/db"
    )


def test_normalize_dsn_is_idempotent():
    clean = "postgresql://u:p@host:5432/db"
    assert dsn.normalize_dsn(clean) == clean
    assert dsn.normalize_dsn(dsn.normalize_dsn("postgresql+asyncpg://x")) == "postgresql://x"


def test_normalize_dsn_handles_empty_and_none():
    assert dsn.normalize_dsn("") == ""
    assert dsn.normalize_dsn(None) == ""


@pytest.mark.asyncio
async def test_connect_uses_normalized_dsn(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_connect(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(dsn.asyncpg, "connect", fake_connect)

    await dsn.connect("postgresql+asyncpg://u:p@host:5432/db", timeout=5)

    assert captured["url"] == "postgresql://u:p@host:5432/db"
    assert captured["kwargs"] == {"timeout": 5}
