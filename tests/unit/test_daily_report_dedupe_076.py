"""Story-076: o relatorio diario sai 1x por grupo/dia, mesmo com varios tenants/schedulers.

Causa real: cada tenant registra seu proprio scheduler (api/main.py lifespan) apontando para o
mesmo REPORT_WA_GROUP_ID -> o grupo recebia o relatorio N vezes (uma por tenant), com ~1s de
diferenca. A guarda de idempotencia por (grupo+dia) garante 1 envio.
"""
from __future__ import annotations

import asyncio

import pytest

from zwaf.reporting import daily_report
from zwaf.reporting.daily_report import build_and_send_report, reset_daily_report_dedupe

GROUP = "5511999999999@g.us"


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sends: list[dict] = []

    async def send_message(self, *, phone, text, session_id):
        self.sends.append({"phone": phone, "text": text, "session_id": session_id})
        return True


@pytest.fixture(autouse=True)
def _clean_dedupe():
    reset_daily_report_dedupe()
    yield
    reset_daily_report_dedupe()


@pytest.mark.asyncio
async def test_two_tenants_same_group_sends_once():
    """AC-1: dois tenants disparando para o MESMO grupo no mesmo dia -> 1 envio."""
    wa = FakeWhatsApp()
    await build_and_send_report(db_url=None, tenant_id="livia-raiz-vital", whatsapp_tool=wa, group_id=GROUP)
    await build_and_send_report(db_url=None, tenant_id="caio-alpha-pulse", whatsapp_tool=wa, group_id=GROUP)
    assert len(wa.sends) == 1


@pytest.mark.asyncio
async def test_concurrent_dispatch_sends_once():
    """AC-2: dois schedulers concorrentes (mesmo event loop) -> 1 envio (slot reservado antes do await)."""
    wa = FakeWhatsApp()
    await asyncio.gather(
        build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP),
        build_and_send_report(db_url=None, tenant_id="t2", whatsapp_tool=wa, group_id=GROUP),
    )
    assert len(wa.sends) == 1


@pytest.mark.asyncio
async def test_different_groups_both_send():
    """Tenants com GRUPOS distintos continuam recebendo cada um o seu relatorio."""
    wa = FakeWhatsApp()
    await build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP)
    await build_and_send_report(db_url=None, tenant_id="t2", whatsapp_tool=wa, group_id="OUTRO@g.us")
    assert len(wa.sends) == 2


@pytest.mark.asyncio
async def test_send_failure_releases_slot_for_retry(monkeypatch):
    """Se o envio falhar, o slot e liberado para nova tentativa no mesmo dia."""
    class FailingThenOk:
        def __init__(self):
            self.calls = 0
            self.sends = []

        async def send_message(self, *, phone, text, session_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("network down")
            self.sends.append(phone)
            return True

    wa = FailingThenOk()
    await build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP)
    assert wa.sends == []  # 1a tentativa falhou
    await build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP)
    assert wa.sends == [GROUP]  # retry no mesmo dia funciona


@pytest.mark.asyncio
async def test_new_day_sends_again(monkeypatch):
    """Vira o dia -> novo relatorio e enviado (a guarda e por dia)."""
    wa = FakeWhatsApp()
    monkeypatch.setattr(daily_report, "_today_brt", lambda: "01/01/2026")
    await build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP)
    monkeypatch.setattr(daily_report, "_today_brt", lambda: "02/01/2026")
    await build_and_send_report(db_url=None, tenant_id="t1", whatsapp_tool=wa, group_id=GROUP)
    assert len(wa.sends) == 2
