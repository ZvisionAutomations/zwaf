"""Unit tests for reactivation/recovery metrics (story-044, F6)."""
from __future__ import annotations

import pytest

from zwaf.reporting import reactivation_report


class FakeConn:
    def __init__(self, *, open_opp, recovered, repeat, with_memory):
        self._open = open_opp
        self._recovered = recovered
        self._repeat = repeat
        self._memory = with_memory

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if "NOT EXISTS" in q:
            return self._open
        if "AND EXISTS" in q:
            return self._recovered
        if "HAVING COUNT(*) >= 2" in q:
            return self._repeat
        if "memory_updated_at IS NOT NULL" in q:
            return self._memory
        raise AssertionError(f"unexpected fetchval: {q}")


@pytest.mark.asyncio
async def test_metrics_compute_recovery_rate():
    conn = FakeConn(open_opp=6, recovered=2, repeat=3, with_memory=10)
    m = await reactivation_report.get_reactivation_metrics(conn, "livia-raiz-vital")
    assert m["open_payment_opportunities"] == 6
    assert m["recovered_payments"] == 2
    assert m["recovery_rate"] == pytest.approx(2 / 8)
    assert m["repeat_buyers"] == 3
    assert m["leads_with_memory"] == 10


@pytest.mark.asyncio
async def test_metrics_zero_denominator_is_safe():
    conn = FakeConn(open_opp=0, recovered=0, repeat=0, with_memory=0)
    m = await reactivation_report.get_reactivation_metrics(conn, "t")
    assert m["recovery_rate"] == 0.0


@pytest.mark.asyncio
async def test_build_report_without_db_is_graceful():
    assert await reactivation_report.build_reactivation_report(None, "t") == {}


def test_format_reactivation_report():
    out = reactivation_report.format_reactivation_report(
        {
            "open_payment_opportunities": 6,
            "recovered_payments": 2,
            "recovery_rate": 0.25,
            "repeat_buyers": 3,
            "leads_with_memory": 10,
        }
    )
    assert "Recuperacao" in out
    assert "25,0%" in out
    assert "Clientes recorrentes (2+ compras): 3" in out
