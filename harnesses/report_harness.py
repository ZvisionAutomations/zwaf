"""Harnesses for Sofia Daily Report.

Uso:
    python -m harnesses.report_harness
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zwaf.reporting.daily_report import build_and_send_report, format_report


async def harness_report_dry_run():
    mock_metrics = {
        "conversations_today": 8,
        "sales_today": 3,
        "revenue_today_cents": 59100,
        "total_sales_all_time": 7,
    }
    message = format_report(mock_metrics, date="25/05/2026", initial_stock=600)
    print("=== DRY RUN OUTPUT ===")
    print(message)
    assert "591,00" in message, f"Receita errada: {message}"
    assert "593 potes" in message, f"Estoque errado: {message}"
    print("OK Harness 1 passed")


async def harness_zero_sales():
    mock_metrics = {
        "conversations_today": 0,
        "sales_today": 0,
        "revenue_today_cents": 0,
        "total_sales_all_time": 0,
    }
    message = format_report(mock_metrics, date="25/05/2026")
    assert "R$ 0,00" in message
    assert "600 potes" in message
    print("OK Harness 2 passed")


async def harness_wa_noop():
    """build_and_send_report NAO deve lancar excecao quando WA sem api_key."""
    from zwaf.tools.whatsapp import WhatsAppTool

    with patch.dict(os.environ, {"EVOLUTION_API_KEY": ""}):
        tool = WhatsAppTool(api_key="")
        await build_and_send_report(
            db_url=None,
            tenant_id="livia-raiz-vital",
            whatsapp_tool=tool,
            group_id="mock-group",
        )
    print("OK Harness 3 passed")


class _CaptureWhatsAppTool:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, phone: str, text: str, session_id: str | None = None):
        self.messages.append(text)
        return {"success": True, "phone": phone, "session_id": session_id}


async def harness_db_unavailable():
    """Simula falha de conexao. Mensagem enviada com campos indisponiveis."""
    tool = _CaptureWhatsAppTool()
    await build_and_send_report(
        db_url="postgresql://zwaf:zwaf@127.0.0.1:1/zwaf_unavailable",
        tenant_id="livia-raiz-vital",
        whatsapp_tool=tool,
        group_id="mock-group",
    )
    assert tool.messages, "Mensagem nao foi enviada"
    message = tool.messages[0]
    assert "indisponivel" in message, f"Mensagem parcial nao marcada: {message}"
    print("OK Harness 4 passed")


async def main():
    await harness_report_dry_run()
    await harness_zero_sales()
    await harness_wa_noop()
    await harness_db_unavailable()


if __name__ == "__main__":
    asyncio.run(main())
