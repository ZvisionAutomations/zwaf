"""Dry-run smoke for Livia pre-WhatsApp business rules."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from zwaf.conversion.checkout_policy import (
    is_critical_complaint,
    is_opt_out_message,
    validate_checkout_ready,
)


VALID_DOCUMENT = "123" + "456" + "789" + "01"
VALID_ADDRESS = {
    "postal_code": "01001000",
    "street": "Rua Teste",
    "number": "100",
    "district": "Centro",
    "city": "Sao Paulo",
    "state": "SP",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


async def run_dry_run() -> list[Check]:
    checks: list[Check] = []

    missing = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman-1",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address={"city": "Sao Paulo"},
    )
    checks.append(Check("blocks_missing_structured_address", not missing.ok, missing.code))

    ready = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="new-woman-1",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )
    checks.append(Check("accepts_complete_new_woman_checkout", ready.ok, ready.code))

    alpha = validate_checkout_ready(
        tenant_id="livia-raiz-vital",
        product_id="alpha-pulse-1",
        customer_name="Maria Silva",
        customer_document=VALID_DOCUMENT,
        delivery_address=VALID_ADDRESS,
    )
    checks.append(Check("blocks_alpha_pulse_for_livia", not alpha.ok, alpha.code))

    checks.append(Check("detects_opt_out", is_opt_out_message("nao tenho interesse, remover")))
    checks.append(Check("detects_critical_complaint", is_critical_complaint("quero reembolso")))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    if not args.dry_run:
        return 2

    checks = asyncio.run(run_dry_run())
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        suffix = f" - {check.detail}" if check.detail else ""
        print(f"{status} {check.name}{suffix}")
    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
