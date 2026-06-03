"""Manual delivery marker for Livia MVP fulfillment."""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone


FOLLOWUPS = (
    ("received_usage", 0),
    ("delivery_15d", 15),
    ("delivery_30d_coupon", 30),
)


async def mark_delivery(order_id: str, delivered_by: str, dry_run: bool) -> int:
    delivered_at = datetime.now(timezone.utc)
    if dry_run:
        print(f"DRY-RUN mark order delivered: {order_id}")
        print(f"DRY-RUN delivered_by: {delivered_by}")
        for kind, days in FOLLOWUPS:
            print(f"DRY-RUN schedule {kind}: {(delivered_at + timedelta(days=days)).isoformat()}")
        return 0

    db_url = (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")
    if not db_url:
        print("DATABASE_URL not configured")
        return 2

    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        shipment_id = await conn.fetchval(
            """
            INSERT INTO shipments (order_id, provider, status, delivered_by, delivered_at)
            VALUES ($1, 'manual', 'delivered', $2, $3)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            order_id,
            delivered_by,
            delivered_at,
        )
        if shipment_id is None:
            shipment_id = await conn.fetchval(
                """
                UPDATE shipments
                SET status = 'delivered',
                    delivered_by = $2,
                    delivered_at = COALESCE(delivered_at, $3),
                    updated_at = NOW()
                WHERE order_id = $1
                RETURNING id
                """,
                order_id,
                delivered_by,
                delivered_at,
            )
        if shipment_id is None:
            print("Order not found or shipment could not be created")
            return 1

        await conn.execute(
            """
            INSERT INTO delivery_events (shipment_id, event_type, event_at, source)
            VALUES ($1, 'delivered', $2, 'manual')
            """,
            shipment_id,
            delivered_at,
        )
        for kind, days in FOLLOWUPS:
            await conn.execute(
                """
                INSERT INTO followup_events (order_id, kind, scheduled_for, status)
                VALUES ($1, $2, $3, 'scheduled')
                ON CONFLICT (order_id, kind) DO UPDATE SET
                    scheduled_for = EXCLUDED.scheduled_for,
                    status = CASE
                        WHEN followup_events.sent_at IS NULL THEN 'scheduled'
                        ELSE followup_events.status
                    END,
                    updated_at = NOW()
                """,
                order_id,
                kind,
                delivered_at + timedelta(days=days),
            )
    finally:
        await conn.close()

    print(f"Order {order_id} marked delivered")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--delivered-by", default="Fernando")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(mark_delivery(args.order_id, args.delivered_by, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
