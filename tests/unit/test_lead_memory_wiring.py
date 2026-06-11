"""Wiring tests for lead_memory_block propagation (story-044, LOW-2).

The vendedor builder is already covered by
`test_lead_memory.test_build_agent_appends_memory_block`. This module mirrors
that pattern for the other three agents — recompra, suporte and cobranca — to
prove each builder forwards the `lead_memory_block` all the way into the
resulting agent's instructions (reinjected by `base_agent.build_agent`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from zwaf.agents.cobranca import build_cobranca_agent
from zwaf.agents.recompra import build_recompra_agent
from zwaf.agents.suporte import build_suporte_agent


BLOCK = "## Memória deste lead\n- Nome: Maria"


class FakeWhatsAppTool:
    """Minimal stand-in: the builders only reference these callables to populate
    the tool list — they are never invoked during construction."""

    async def send_message(self, *args, **kwargs):  # pragma: no cover - never called
        return None

    async def _set_typing(self, *args, **kwargs):  # pragma: no cover - never called
        return None


@pytest.fixture
def tenant():
    return SimpleNamespace(
        tenant_id="test-tenant",
        agent_name="Lívia",
        payment={},
        llm=SimpleNamespace(primary="gpt-4o", temperature=0.4),
    )


@pytest.fixture(autouse=True)
def _openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")


@pytest.mark.parametrize(
    "builder",
    [build_recompra_agent, build_suporte_agent, build_cobranca_agent],
    ids=["recompra", "suporte", "cobranca"],
)
def test_builder_propagates_memory_block(builder, tenant):
    agent = builder(
        tenant_config=tenant,
        whatsapp_tool=FakeWhatsAppTool(),
        session_id="s1",
        lead_id="l1",
        db_url="",
        lead_memory_block=BLOCK,
    )
    assert "Memória deste lead" in agent.instructions
    assert "Maria" in agent.instructions


@pytest.mark.parametrize(
    "builder",
    [build_recompra_agent, build_suporte_agent, build_cobranca_agent],
    ids=["recompra", "suporte", "cobranca"],
)
def test_builder_without_block_is_unchanged(builder, tenant):
    agent = builder(
        tenant_config=tenant,
        whatsapp_tool=FakeWhatsAppTool(),
        session_id="s1",
        lead_id="l1",
        db_url="",
    )
    assert "Memória deste lead" not in agent.instructions
