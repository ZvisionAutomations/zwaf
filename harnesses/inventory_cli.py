"""Inventory operations CLI for the Raiz Vital go-live (story-034).

Usage:
    python -m zwaf.harnesses.inventory_cli status --tenant livia-raiz-vital
    python -m zwaf.harnesses.inventory_cli release-expired --tenant livia-raiz-vital
    python -m zwaf.harnesses.inventory_cli adjust --tenant livia-raiz-vital \\
        --product new-woman --delta -3 --reason "perda no estoque fisico" --by Fernando

All commands are idempotent and require DATABASE_URL to be configured.
"""
from __future__ import annotations

import argparse
import asyncio

from zwaf.memory.inventory_store import (
    inventory_status,
    manual_adjustment,
    release_expired,
)


async def _cmd_status(args: argparse.Namespace) -> int:
    rows = await inventory_status(tenant_id=args.tenant, product_id=args.product)
    if not rows:
        print(f"No inventory rows for tenant '{args.tenant}'"
              + (f" / product '{args.product}'" if args.product else "")
              + " (or DATABASE_URL not configured).")
        return 1
    header = f"{'product':<16} {'on_hand':>8} {'reserved':>9} {'committed':>10} {'buffer':>7} {'available':>10}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['product_id']:<16} {row['on_hand_qty']:>8} {row['reserved_qty']:>9} "
            f"{row['committed_qty']:>10} {row['safety_buffer_qty']:>7} {row['available']:>10}"
        )
    return 0


async def _cmd_release_expired(args: argparse.Namespace) -> int:
    released = await release_expired(tenant_id=args.tenant)
    print(f"Released {released} expired reservation(s) for tenant '{args.tenant}'.")
    return 0


async def _cmd_adjust(args: argparse.Namespace) -> int:
    try:
        ok = await manual_adjustment(
            tenant_id=args.tenant,
            product_id=args.product,
            on_hand_delta=args.delta,
            reason=args.reason,
            created_by=args.by,
        )
    except ValueError as exc:
        print(f"Invalid adjustment: {exc}")
        return 2
    if not ok:
        print(
            "Adjustment not applied — product not found, DATABASE_URL missing, "
            "or it would drop on_hand below reserved+committed."
        )
        return 1
    print(
        f"Adjusted '{args.product}' for '{args.tenant}' by {args.delta:+d} "
        f"(reason: {args.reason})."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zwaf inventory", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show stock counters per product")
    p_status.add_argument("--tenant", required=True)
    p_status.add_argument("--product", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_expire = sub.add_parser("release-expired", help="Release reservations past TTL")
    p_expire.add_argument("--tenant", required=True)
    p_expire.set_defaults(func=_cmd_release_expired)

    p_adjust = sub.add_parser("adjust", help="Audited manual on-hand correction")
    p_adjust.add_argument("--tenant", required=True)
    p_adjust.add_argument("--product", required=True)
    p_adjust.add_argument("--delta", type=int, required=True)
    p_adjust.add_argument("--reason", required=True)
    p_adjust.add_argument("--by", required=True, help="Operator name for the audit trail")
    p_adjust.set_defaults(func=_cmd_adjust)

    return parser


def main() -> int:
    args = _build_parser().parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
