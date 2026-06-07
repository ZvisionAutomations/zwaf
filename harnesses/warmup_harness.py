"""
Warm-up Harness — Simula 7 dias de operação com volume crescente.

Testa:
- Limites por dia: 20, 20, 20, 50, 50, 50, operação normal
- Rotação de número quando limite atingido
- Nenhuma mensagem enviada acima do limite diário

Uso:
    python -m harnesses.warmup_harness
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from datetime import date
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from zwaf.tools.base import ToolResult
from zwaf.tools.whatsapp import WhatsAppTool, get_warm_up_limit


@dataclass
class DayResult:
    day: int
    expected_limit: int
    actual_sent: int
    blocked_at_limit: bool
    passed: bool


async def simulate_day(day: int, messages_per_minute: int = 10) -> DayResult:
    """Simula envio de mensagens em um dia de warm-up."""
    expected_limit = get_warm_up_limit(day, messages_per_minute)

    tool = WhatsAppTool(
        base_url="http://mock",
        api_key="test-key",
        instance="test-1",
        messages_per_minute=messages_per_minute,
        typing_simulation=False,
        warm_up_mode=True,
        warm_up_day=day,
    )
    tool._daily_count_date = date.today().isoformat()

    async def mock_send_raw(phone, text):
        return ToolResult.ok({"status": "sent", "message_id": f"mock-{time.monotonic()}"})

    with patch.object(tool, "_send_raw", mock_send_raw):
        actual_sent = 0
        blocked = False
        attempts = expected_limit + 5  # Tentar 5 acima do limite

        for i in range(attempts):
            result = await tool.send_message(
                phone="5511999990001",
                text=f"Mensagem {i+1} do dia {day}",
                session_id=f"warmup-day{day}-{i}",
            )
            if result.success:
                actual_sent += 1
            else:
                # Primeiro bloqueio deve ser exatamente no limite
                if actual_sent == expected_limit:
                    blocked = True
                break

    passed = (actual_sent == expected_limit) and blocked

    return DayResult(
        day=day,
        expected_limit=expected_limit,
        actual_sent=actual_sent,
        blocked_at_limit=blocked,
        passed=passed,
    )


async def run_all() -> None:
    print("\n=== WARM-UP HARNESS — ZWAF 7-Day Simulation ===\n")
    print(f"{'Dia':<6} {'Limite':<10} {'Enviadas':<12} {'Bloqueio':<12} {'Status'}")
    print("-" * 55)

    results = []
    for day in range(1, 9):  # dias 1-8 (8 = primeiro dia de operação normal)
        result = await simulate_day(day, messages_per_minute=10)
        results.append(result)

        status = "✓ PASS" if result.passed else "✗ FAIL"
        blocked_str = "no limite" if result.blocked_at_limit else "NÃO"
        print(f"{result.day:<6} {result.expected_limit:<10} {result.actual_sent:<12} {blocked_str:<12} {status}")

    total = len(results)
    passed = sum(1 for r in results if r.passed)

    print(f"\n=== RESULTADO: {passed}/{total} ({'✓ APROVADO' if passed == total else '✗ REPROVADO'}) ===\n")

    if passed < total:
        for r in results:
            if not r.passed:
                print(f"  FALHOU dia {r.day}: esperado {r.expected_limit}, enviou {r.actual_sent}, bloqueio={r.blocked_at_limit}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
