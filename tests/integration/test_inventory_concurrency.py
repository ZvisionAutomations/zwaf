"""Postgres concurrency test for inventory reservations (story-034).

Two checkouts race for the last unit; exactly one reservation must win and
``available`` must never go negative. This is the scenario the in-memory unit
fakes cannot prove — it needs a real row lock.

Requires a live test database. Set ``ZWAF_TEST_DATABASE_URL`` (schema with
migration 004 applied) to enable; otherwise the module is skipped.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

TEST_DB = os.getenv("ZWAF_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="ZWAF_TEST_DATABASE_URL not configured"
)


async def _seed(conn, tenant: str, product: str, on_hand: int) -> list[str]:
    await conn.execute(
        """
        INSERT INTO inventory_items (tenant_id, product_id, on_hand_qty)
        VALUES ($1, $2, $3)
        ON CONFLICT (tenant_id, product_id)
        DO UPDATE SET on_hand_qty = EXCLUDED.on_hand_qty,
                      reserved_qty = 0, committed_qty = 0
        """,
        tenant,
        product,
        on_hand,
    )
    order_ids = []
    for _ in range(2):
        oid = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO orders (id, tenant_id, lead_phone, product_id, quantity, status)
            VALUES ($1, $2, '5511999990001', $3, 1, 'checkout_ready')
            """,
            oid,
            tenant,
            product,
        )
        order_ids.append(oid)
    return order_ids


async def _cleanup(conn, tenant: str, product: str, order_ids: list[str]) -> None:
    await conn.execute("DELETE FROM inventory_movements WHERE tenant_id = $1", tenant)
    await conn.execute(
        "DELETE FROM inventory_reservations WHERE order_id = ANY($1::uuid[])", order_ids
    )
    await conn.execute("DELETE FROM orders WHERE id = ANY($1::uuid[])", order_ids)
    await conn.execute(
        "DELETE FROM inventory_items WHERE tenant_id = $1 AND product_id = $2",
        tenant,
        product,
    )


@pytest.mark.asyncio
async def test_concurrent_checkout_for_last_unit_reserves_once(monkeypatch):
    import asyncpg

    from zwaf.memory import inventory_store

    db_url = TEST_DB.replace("+asyncpg", "")
    monkeypatch.setenv("DATABASE_URL", db_url)

    tenant = f"itest-{uuid.uuid4().hex[:8]}"
    product = "new-woman"

    conn = await asyncpg.connect(db_url)
    try:
        order_ids = await _seed(conn, tenant, product, on_hand=1)

        # Both fire at once for the single remaining unit.
        results = await asyncio.gather(
            inventory_store.reserve_inventory(
                tenant_id=tenant, product_id=product, quantity=1, order_id=order_ids[0]
            ),
            inventory_store.reserve_inventory(
                tenant_id=tenant, product_id=product, quantity=1, order_id=order_ids[1]
            ),
        )

        statuses = sorted(r.status for r in results)
        assert statuses == ["reserved", "unavailable"]
        assert sum(1 for r in results if r.ok and r.status == "reserved") == 1

        row = await conn.fetchrow(
            """
            SELECT reserved_qty,
                   on_hand_qty - reserved_qty - committed_qty - safety_buffer_qty AS available
            FROM inventory_items WHERE tenant_id = $1 AND product_id = $2
            """,
            tenant,
            product,
        )
        assert row["reserved_qty"] == 1
        assert row["available"] == 0  # never negative

        active = await conn.fetchval(
            "SELECT COUNT(*) FROM inventory_reservations WHERE order_id = ANY($1::uuid[]) AND status = 'active'",
            order_ids,
        )
        assert active == 1
    finally:
        await _cleanup(conn, tenant, product, order_ids)
        await conn.close()
