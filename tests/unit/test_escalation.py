"""Tests for human escalation tool behavior."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zwaf.tools import escalation
from zwaf.tools import whatsapp


@pytest.mark.asyncio
async def test_escalate_to_human_returns_lead_confirmation_without_tool(monkeypatch):
    monkeypatch.setattr(whatsapp, "_tenant_tools", {})

    message = await escalation.escalate_to_human(
        lead_phone="5511000000000",
        lead_name="Lead Teste",
        problem_summary="precisa de suporte",
        escalation_phone="5511000000001",
    )

    assert "Fernando" in message
    assert "entrar em contato" in message


@pytest.mark.asyncio
async def test_escalate_to_human_sends_context_with_available_tool(monkeypatch):
    send_message = AsyncMock()
    fake_tool = type("FakeWhatsAppTool", (), {"send_message": send_message})()
    monkeypatch.setattr(whatsapp, "_tenant_tools", {"tenant-test": fake_tool})

    message = await escalation.escalate_to_human(
        lead_phone="5511000000000",
        lead_name="Lead Teste",
        problem_summary="pedido com erro",
        conversation_history="lead pediu ajuda",
        escalation_phone="5511000000001",
        agent_name="Agente Teste",
    )

    assert "Fernando" in message
    send_message.assert_awaited_once()
    _, kwargs = send_message.call_args
    assert kwargs["phone"] == "5511000000001"
    assert "pedido com erro" in kwargs["text"]
    assert "lead pediu ajuda" in kwargs["text"]
