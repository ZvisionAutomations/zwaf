"""Asaas webhook end-to-end smoke harness.

Creates a short-lived production payment, waits for the real Asaas webhook to
reach ZWAF, then deletes the payment/customer and removes smoke DB rows.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, timedelta
from typing import Any

import asyncpg
import httpx


TENANT_ID = "livia-raiz-vital"
PHONE = "5511999990002"
PRODUCT_ID = "new-woman-1"
EXTERNAL_ID = "nw-001-e2e-20260603-001"
CUSTOMER_REF = f"{TENANT_ID}:{PHONE}"
PAYMENT_REF = f"{TENANT_ID}:{PHONE}:{PRODUCT_ID}:{EXTERNAL_ID}"
TARGET_PRODUCTION_URL = "https://api.asaas.com/v3"


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": _env("ASAAS_USER_AGENT") or "zwaf-raiz-vital",
        "access_token": _env("ASAAS_API_KEY"),
    }


def _data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("data", [])
    return value if isinstance(value, list) else []


async def _count_events(conn: asyncpg.Connection, payment_id: str, status: str | None = None) -> int:
    if status:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM payment_events
            WHERE tenant_id=$1 AND payment_id=$2 AND provider=$3 AND status=$4
            """,
            TENANT_ID,
            payment_id,
            "asaas",
            status,
        )
    return await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM payment_events
        WHERE tenant_id=$1 AND payment_id=$2 AND provider=$3
        """,
        TENANT_ID,
        payment_id,
        "asaas",
    )


async def _wait_for_event(
    conn: asyncpg.Connection,
    payment_id: str,
    status: str,
    attempts: int,
    interval_seconds: int,
) -> bool:
    for _ in range(attempts):
        if await _count_events(conn, payment_id, status):
            return True
        await asyncio.sleep(interval_seconds)
    return False


async def run(args: argparse.Namespace) -> int:
    base_url = _env("ASAAS_BASE_URL").rstrip("/")
    document = _env("ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ")
    db_url = _env("DATABASE_URL").replace("+asyncpg", "")
    if not args.execute:
        print("execute=false")
        print("No external calls were made. Use --execute --allow-production.")
        return 0
    if base_url == TARGET_PRODUCTION_URL and not args.allow_production:
        print("blocked=production_requires_allow_production")
        return 2
    if base_url != TARGET_PRODUCTION_URL or not _env("ASAAS_API_KEY") or not document or not db_url:
        print("e2e_config=missing_or_not_production")
        return 2

    customer_id = ""
    payment_id = ""
    created_seen = False
    deleted_seen = False

    async with httpx.AsyncClient(timeout=20.0) as client:
        customer_resp = await client.post(
            f"{base_url}/customers",
            headers=_headers(),
            json={
                "name": "Cliente Smoke ZWAF E2E",
                "mobilePhone": PHONE,
                "cpfCnpj": document,
                "externalReference": CUSTOMER_REF,
            },
        )
        customer_resp.raise_for_status()
        customer_id = str(customer_resp.json().get("id", ""))
        print("customer_created=yes" if customer_id else "customer_created=no")

        payment_resp = await client.post(
            f"{base_url}/payments",
            headers=_headers(),
            json={
                "customer": customer_id,
                "billingType": "PIX",
                "value": 165.90,
                "dueDate": (date.today() + timedelta(days=2)).isoformat(),
                "description": "ZWAF webhook E2E smoke",
                "externalReference": PAYMENT_REF,
            },
        )
        payment_resp.raise_for_status()
        payment_id = str(payment_resp.json().get("id", ""))
        print("payment_created=yes" if payment_id else "payment_created=no")

        conn = await asyncpg.connect(db_url)
        try:
            created_seen = await _wait_for_event(
                conn,
                payment_id,
                "PENDING",
                attempts=args.created_attempts,
                interval_seconds=args.interval_seconds,
            )
            print(f"webhook_payment_created_seen={str(created_seen).lower()}")
        finally:
            await conn.close()

        if payment_id:
            delete_payment_resp = await client.delete(f"{base_url}/payments/{payment_id}", headers=_headers())
            delete_payment_resp.raise_for_status()
            print("payment_deleted=yes")
        if customer_id:
            delete_customer_resp = await client.delete(f"{base_url}/customers/{customer_id}", headers=_headers())
            delete_customer_resp.raise_for_status()
            print("customer_deleted=yes")

        conn = await asyncpg.connect(db_url)
        try:
            if payment_id:
                deleted_seen = await _wait_for_event(
                    conn,
                    payment_id,
                    "CANCELLED",
                    attempts=args.deleted_attempts,
                    interval_seconds=args.interval_seconds,
                )
                print(f"webhook_payment_deleted_seen={str(deleted_seen).lower()}")
                total_before_cleanup = await _count_events(conn, payment_id)
                print(f"db_events_before_cleanup={total_before_cleanup}")
                cleanup = await conn.execute(
                    """
                    DELETE FROM payment_events
                    WHERE tenant_id=$1 AND payment_id=$2 AND provider=$3
                    """,
                    TENANT_ID,
                    payment_id,
                    "asaas",
                )
                print(f"db_cleanup_status={cleanup}")
                total_after_cleanup = await _count_events(conn, payment_id)
                print(f"db_events_after_cleanup={total_after_cleanup}")
        finally:
            await conn.close()

        payments_check = await client.get(
            f"{base_url}/payments",
            headers=_headers(),
            params={"externalReference": PAYMENT_REF, "limit": 10},
        )
        payments_check.raise_for_status()
        remaining_payments = [
            payment
            for payment in _data(payments_check.json())
            if payment.get("externalReference") == PAYMENT_REF
        ]
        print(f"asaas_payments_remaining={len(remaining_payments)}")

        customers_check = await client.get(
            f"{base_url}/customers",
            headers=_headers(),
            params={"externalReference": CUSTOMER_REF, "limit": 10},
        )
        customers_check.raise_for_status()
        remaining_customers = [
            customer
            for customer in _data(customers_check.json())
            if customer.get("externalReference") == CUSTOMER_REF
        ]
        print(f"asaas_customers_remaining={len(remaining_customers)}")

    if not customer_id or not payment_id or not created_seen:
        return 1
    if args.require_deleted_event and not deleted_seen:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ZWAF Asaas webhook E2E smoke")
    parser.add_argument("--execute", action="store_true", help="Create/delete a real Asaas payment")
    parser.add_argument("--allow-production", action="store_true", help="Allow production Asaas base URL")
    parser.add_argument("--require-deleted-event", action="store_true")
    parser.add_argument("--created-attempts", type=int, default=18)
    parser.add_argument("--deleted-attempts", type=int, default=6)
    parser.add_argument("--interval-seconds", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
