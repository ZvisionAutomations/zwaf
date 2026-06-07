"""Dry-run harness for Story 038: Livia follow-up and self-improvement loop.

Usage:
    python -m harnesses.livia_followup_story_038 --dry-run
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from zwaf.conversion.followup import LeadTemperature, build_followup_plan, classify_lead_temperature
from zwaf.conversion.self_improvement import ImprovementKind, ImprovementQueue, ImprovementStatus
from zwaf.reporting.commercial_report import format_commercial_daily_report


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def run_dry_run() -> list[Check]:
    checks: list[Check] = []

    hot_plan = build_followup_plan(
        messages="Tenho calor, sono ruim e quero comprar",
        stage="post_offer",
    )
    checks.append(Check("hot_abandoned_post_offer_max_5", hot_plan.allowed and hot_plan.max_contacts == 5))

    warm_plan = build_followup_plan(messages="Qual o valor?", stage="post_offer")
    checks.append(Check("warm_price_question_max_3", warm_plan.allowed and warm_plan.max_contacts == 3))

    cold_plan = build_followup_plan(messages="Oi, tudo bem?", stage="post_offer")
    checks.append(Check("cold_greeting_max_1", cold_plan.allowed and cold_plan.max_contacts == 1))

    risk_plan = build_followup_plan(messages="Tomo remedio e tive reacao", stage="post_offer")
    checks.append(Check("risk_blocks_commercial_followup", not risk_plan.allowed and risk_plan.reason == "medical_risk"))

    opt_out_plan = build_followup_plan(messages="Nao tenho interesse, pode remover", stage="post_offer")
    checks.append(Check("opt_out_interrupts_followup", not opt_out_plan.allowed and opt_out_plan.reason == "opt_out"))

    queue = ImprovementQueue()
    candidate = queue.suggest(
        kind=ImprovementKind.COPY,
        summary="Nova copy para objecao de preco",
        evidence={"objection": "preco"},
    )
    checks.append(Check("copy_improvement_stays_suggested", candidate.status is ImprovementStatus.SUGGESTED))

    report = format_commercial_daily_report(
        {
            "leads_attended": 10,
            "hot_leads": 3,
            "payments_confirmed": 2,
            "pots_sold": 4,
            "top_objections": ["preco", "entrega"],
            "actions": {"human_needed": "lead-session-42"},
        }
    )
    checks.append(Check("commercial_report_has_no_email", "@" not in report))

    risk_temperature = classify_lead_temperature("Tomo medicacao").temperature
    checks.append(Check("medical_terms_classify_risk", risk_temperature is LeadTemperature.RISK))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    if not args.dry_run:
        return 2

    checks = run_dry_run()
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        suffix = f" - {check.detail}" if check.detail else ""
        print(f"{status} {check.name}{suffix}")
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
