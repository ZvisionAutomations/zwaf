"""SuperFrete smoke harness.

Default is dry-run. The harness never buys a label unless --create-label and
--yes-create-label are both present.
"""
from __future__ import annotations

import argparse
import asyncio
import os

from zwaf.shipping.service import create_label_for_order, quote_shipping
from zwaf.shipping.superfrete import package_from_env, sender_from_env


async def run(args: argparse.Namespace) -> int:
    sender = sender_from_env()
    if args.dry_run:
        print("DRY-RUN SuperFrete smoke")
        print(f"configured_token: {bool(os.getenv('SUPERFRETE_TOKEN'))}")
        print(f"base_url: {os.getenv('SUPERFRETE_BASE_URL', 'https://sandbox.superfrete.com')}")
        print(f"user_agent_configured: {bool(os.getenv('SUPERFRETE_USER_AGENT'))}")
        print(f"from_postal_code_configured: {bool(sender.get('postal_code'))}")
        print(f"auto_checkout_enabled: {os.getenv('SUPERFRETE_AUTO_CHECKOUT_ENABLED', '').lower() == 'true'}")
        print(f"sample_package: {package_from_env(args.quantity)}")
        print("No SuperFrete request was sent.")
        return 0

    if not os.getenv("SUPERFRETE_TOKEN"):
        print("SUPERFRETE_TOKEN not configured")
        return 2

    quote = await quote_shipping(
        to_postal_code=args.to_postal_code,
        quantity=args.quantity,
        services=args.services,
    )
    print(f"quote_services: {len(quote.get('services', []))}")

    if not args.create_label:
        print("Label creation skipped. Pass --create-label --yes-create-label to execute it.")
        return 0
    if not args.yes_create_label:
        print("Refusing label creation without --yes-create-label")
        return 2
    if not args.order_id:
        print("--order-id is required for label creation")
        return 2

    result = await create_label_for_order(
        order_id=args.order_id,
        service_id=args.service_id,
        execute_checkout=True,
    )
    print(f"label_status: {result.get('status')}")
    print(f"provider_order_id: {result.get('provider_order_id', '')}")
    print(f"tracking_code_present: {bool(result.get('tracking_code'))}")
    print(f"label_url_present: {bool(result.get('label_url'))}")
    if result.get("status") == "manual_fulfillment_pending":
        print("Auto checkout disabled. Emit the label manually in SuperFrete.")
        return 0
    return 0 if result.get("status") == "label_created" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--to-postal-code", default="20020050")
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--services", default=os.getenv("SUPERFRETE_SERVICES", "1,2,17"))
    parser.add_argument("--create-label", action="store_true")
    parser.add_argument("--yes-create-label", action="store_true")
    parser.add_argument("--order-id", default="")
    parser.add_argument("--service-id", type=int, default=1)
    args = parser.parse_args()
    if not args.execute:
        args.dry_run = True
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
