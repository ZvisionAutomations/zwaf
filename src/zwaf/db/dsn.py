"""Single source of truth for DSN normalization and asyncpg connections.

The tenant ``DATABASE_URL`` is stored in SQLAlchemy dialect form
(``postgresql+asyncpg://...``) because the Agno/SQLAlchemy layer in
``build_team`` requires it. But the data-access modules connect with asyncpg
directly, and asyncpg rejects the ``+asyncpg`` suffix
(``invalid DSN: scheme is expected to be either "postgresql" or "postgres"``).

Centralizing the normalization here prevents the class of bug that left the
commercial follow-up engine inert in production (story-081): every module that
opens an asyncpg connection MUST route its DSN through :func:`normalize_dsn`
(directly or via :func:`connect`), instead of re-implementing the replacement
locally.
"""
from __future__ import annotations

from typing import Any

import asyncpg


def normalize_dsn(url: str | None) -> str:
    """Return a DSN that asyncpg accepts.

    Removes the SQLAlchemy ``+asyncpg`` dialect suffix. Idempotent; tolerates
    ``None``/empty (returns ``""``).
    """
    return (url or "").replace("+asyncpg", "")


async def connect(url: str | None, **kwargs: Any) -> "asyncpg.Connection":
    """Open an asyncpg connection using a normalized DSN.

    Thin wrapper over :func:`asyncpg.connect` that applies
    :func:`normalize_dsn` first. Extra keyword arguments are forwarded.
    """
    return await asyncpg.connect(normalize_dsn(url), **kwargs)
