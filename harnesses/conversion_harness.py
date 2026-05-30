"""Harness for ZWAF conversion intelligence.

Usage:
    python -m harnesses.conversion_harness
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from zwaf.conversion.intelligence import BuyingIntent, ConversionAction, analyze_message
from zwaf.conversion.payment_gate import make_guarded_payment_link_generator


@dataclass
class Case:
    name: str
    message: str
    expected_intent: BuyingIntent
    expected_action: ConversionAction
    should_send_link: bool


CASES = [
    Case(
        name="high_intent_link",
        message="Quero comprar o New Woman, pode mandar o link?",
        expected_intent=BuyingIntent.HIGH,
        expected_action=ConversionAction.SEND_PAYMENT_LINK,
        should_send_link=True,
    ),
    Case(
        name="price_objection",
        message="Achei caro, tem desconto?",
        expected_intent=BuyingIntent.MEDIUM,
        expected_action=ConversionAction.HANDLE_OBJECTION,
        should_send_link=False,
    ),
    Case(
        name="health_risk",
        message="Tomei e tive alergia, estou passando mal",
        expected_intent=BuyingIntent.NONE,
        expected_action=ConversionAction.ESCALATE_HUMAN,
        should_send_link=False,
    ),
    Case(
        name="alpha_transfer",
        message="Quero comprar Alpha Pulse",
        expected_intent=BuyingIntent.MEDIUM,
        expected_action=ConversionAction.TRANSFER_AGENT,
        should_send_link=False,
    ),
]


async def _check_payment_gate() -> list[str]:
    failures: list[str] = []
    generator = make_guarded_payment_link_generator("livia-raiz-vital", {})

    blocked = await generator("new-woman-1", "5511999990001", "quanto custa?")
    if "confirma" not in blocked.lower():
        failures.append("payment gate should ask confirmation when evidence is weak")

    alpha = await generator("alpha-pulse-1", "5511999990001", "quero comprar alpha pulse")
    if "consultor correto" not in alpha.lower():
        failures.append("payment gate should block Alpha Pulse for Livia")

    return failures


async def main() -> int:
    failures: list[str] = []

    print("\n=== ZWAF CONVERSION HARNESS ===\n")
    for case in CASES:
        signal = analyze_message(
            case.message,
            tenant_id="livia-raiz-vital",
            agent_name="vendedor",
        )
        ok = (
            signal.buying_intent == case.expected_intent
            and signal.action == case.expected_action
            and signal.should_send_payment_link == case.should_send_link
        )
        status = "PASS" if ok else "FAIL"
        print(f"{status} {case.name}: {signal.to_dict()}")
        if not ok:
            failures.append(case.name)

    failures.extend(await _check_payment_gate())

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\n=== RESULTADO: APROVADO ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
