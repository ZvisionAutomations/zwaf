"""Unit tests for the transactional inventory store (story-034).

A faithful in-memory fake stands in for asyncpg so reservation state machine and
availability math are exercised without a live Postgres. Concurrency (the row
lock that prevents oversell) is verified separately in the integration test.
"""
from __future__ import annotations

import asyncpg
import pytest

from zwaf.memory import inventory_store


# ---------------------------------------------------------------------------
# In-memory fake connection
# ---------------------------------------------------------------------------


class _Tx:
    def __init__(self, conn: "FakeConn"):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return None


class FakeConn:
    """Simulates the exact queries issued by inventory_store."""

    def __init__(self, store: "FakeStore"):
        self.store = store

    def transaction(self):
        return _Tx(self)

    async def close(self):
        return None

    def _available(self, item: dict) -> int:
        return (
            item["on_hand_qty"]
            - item["reserved_qty"]
            - item["committed_qty"]
            - item["safety_buffer_qty"]
        )

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "SELECT id, status FROM inventory_reservations WHERE order_id = $1" in q:
            res = self.store.reservations.get(args[0])
            return None if res is None else dict(res)
        if "JOIN orders o ON o.id = r.order_id" in q:
            tenant, payment_id = args
            for res in self.store.reservations.values():
                order = self.store.orders.get(res["order_id"])
                if order and order["tenant_id"] == tenant and order.get("asaas_payment_id") == payment_id:
                    return dict(res)
            return None
        if "WHERE order_id = $1 AND status = 'active'" in q:
            res = self.store.reservations.get(args[0])
            if res and res["status"] == "active":
                return dict(res)
            return None
        if "SELECT id, product_id FROM orders" in q:
            tenant, payment_id = args
            for oid, order in self.store.orders.items():
                if order["tenant_id"] == tenant and order.get("asaas_payment_id") == payment_id:
                    return {"id": oid, "product_id": order["product_id"]}
            return None
        if "SELECT id FROM orders WHERE tenant_id = $1 AND asaas_payment_id = $2" in q:
            tenant, payment_id = args
            for oid, order in self.store.orders.items():
                if order["tenant_id"] == tenant and order.get("asaas_payment_id") == payment_id:
                    return {"id": oid}
            return None
        raise AssertionError(f"unexpected fetchrow: {q}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if "SELECT order_id FROM inventory_reservations" in q:
            tenant, cutoff = args
            return [
                {"order_id": oid}
                for oid, res in self.store.reservations.items()
                if res["tenant_id"] == tenant
                and res["status"] == "active"
                and res["reserved_until"] < cutoff
            ]
        if "FROM inventory_items" in q and "AS available" in q:
            tenant = args[0]
            product = args[1] if len(args) > 1 else None
            rows = []
            for (t, p), item in sorted(self.store.items.items()):
                if t != tenant or (product and p != product):
                    continue
                rows.append({
                    "tenant_id": t,
                    "product_id": p,
                    "on_hand_qty": item["on_hand_qty"],
                    "reserved_qty": item["reserved_qty"],
                    "committed_qty": item["committed_qty"],
                    "safety_buffer_qty": item["safety_buffer_qty"],
                    "available": self._available(item),
                })
            return rows
        raise AssertionError(f"unexpected fetch: {q}")

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if "INSERT INTO inventory_reservations" in q and "RETURNING id" in q:
            order_id, tenant, product, quantity, reserved_until, idem = args
            rid = self.store.new_id()
            self.store.reservations[order_id] = {
                "id": rid,
                "order_id": order_id,
                "tenant_id": tenant,
                "product_id": product,
                "quantity": quantity,
                "status": "active",
                "reserved_until": reserved_until,
                "idempotency_key": idem,
            }
            return rid
        raise AssertionError(f"unexpected fetchval: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if "SET reserved_qty = reserved_qty + $3" in q:
            tenant, product, qty = args
            item = self.store.items.get((tenant, product))
            if item is None or self._available(item) < qty:
                return "UPDATE 0"
            item["reserved_qty"] += qty
            return "UPDATE 1"
        if "committed_qty = committed_qty + $3" in q:
            tenant, product, qty = args
            item = self.store.items[(tenant, product)]
            item["reserved_qty"] -= qty
            item["committed_qty"] += qty
            return "UPDATE 1"
        if "SET reserved_qty = reserved_qty - $3" in q:
            tenant, product, qty = args
            item = self.store.items[(tenant, product)]
            item["reserved_qty"] -= qty
            return "UPDATE 1"
        if "on_hand_qty = on_hand_qty + $3" in q:
            tenant, product, delta = args
            item = self.store.items.get((tenant, product))
            if item is None:
                return "UPDATE 0"
            item["on_hand_qty"] += delta
            return "UPDATE 1"
        if "UPDATE inventory_reservations SET status = $2" in q:
            rid, new_status = args
            for res in self.store.reservations.values():
                if res["id"] == rid:
                    res["status"] = new_status
            return "UPDATE 1"
        if "SET status = 'confirmed'" in q:
            rid = args[0]
            for res in self.store.reservations.values():
                if res["id"] == rid:
                    res["status"] = "confirmed"
            return "UPDATE 1"
        if "SET status = 'stock_reserved'" in q:
            oid = args[0]
            if oid in self.store.orders:
                self.store.orders[oid]["status"] = "stock_reserved"
            return "UPDATE 1"
        if "SET status = 'manual_review'" in q:
            oid = args[0]
            if oid in self.store.orders:
                self.store.orders[oid]["status"] = "manual_review"
            return "UPDATE 1"
        if "INSERT INTO inventory_movements" in q:
            self.store.movements.append({
                "tenant_id": args[0],
                "product_id": args[1],
                "order_id": args[2],
                "reservation_id": args[3],
                "movement_type": args[4],
                "quantity_delta": args[5],
                "reason": args[6],
                "created_by": args[7],
            })
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {q}")


class FakeStore:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.reservations: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.movements: list[dict] = []
        self._seq = 0

    def new_id(self) -> str:
        self._seq += 1
        return f"res-{self._seq}"

    def add_item(self, tenant, product, on_hand, reserved=0, committed=0, buffer=0):
        self.items[(tenant, product)] = {
            "on_hand_qty": on_hand,
            "reserved_qty": reserved,
            "committed_qty": committed,
            "safety_buffer_qty": buffer,
        }

    def add_order(self, order_id, tenant, product, asaas_payment_id=None, status="checkout_ready"):
        self.orders[order_id] = {
            "tenant_id": tenant,
            "product_id": product,
            "asaas_payment_id": asaas_payment_id,
            "status": status,
        }

    def movement_types(self):
        return [m["movement_type"] for m in self.movements]


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()

    async def fake_connect(_db_url):
        return FakeConn(s)

    monkeypatch.setenv("DATABASE_URL", "postgresql://zwaf:test@postgres:5432/zwaf")
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    return s


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_succeeds_and_decrements_available(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman")

    result = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )

    assert result.ok is True
    assert result.status == "reserved"
    item = store.items[("livia-raiz-vital", "new-woman")]
    assert item["reserved_qty"] == 3
    assert store.reservations["o1"]["status"] == "active"
    assert "reserved" in store.movement_types()
    assert store.orders["o1"]["status"] == "stock_reserved"


@pytest.mark.asyncio
async def test_reserve_blocks_when_insufficient_stock(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=2)
    store.add_order("o1", "livia-raiz-vital", "new-woman")

    result = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )

    assert result.ok is False
    assert result.status == "unavailable"
    assert store.items[("livia-raiz-vital", "new-woman")]["reserved_qty"] == 0
    assert "o1" not in store.reservations


@pytest.mark.asyncio
async def test_reserve_respects_safety_buffer(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10, buffer=8)
    store.add_order("o1", "livia-raiz-vital", "new-woman")

    # available = 10 - 0 - 0 - 8 = 2, asking 3 -> blocked
    result = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )
    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_reserve_is_idempotent_for_same_order(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman")

    first = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )
    second = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )

    assert first.status == "reserved"
    assert second.ok is True
    assert second.status == "already_reserved"
    # Not double counted.
    assert store.items[("livia-raiz-vital", "new-woman")]["reserved_qty"] == 3


@pytest.mark.asyncio
async def test_reserve_skipped_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )
    assert result.ok is True
    assert result.status == "skipped_no_db"


# ---------------------------------------------------------------------------
# Confirmation (webhook paid)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_moves_reserved_to_committed(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman", asaas_payment_id="pay_1")
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )

    conn = FakeConn(store)
    outcome = await inventory_store.confirm_sale_for_payment_conn(
        conn, tenant_id="livia-raiz-vital", payment_id="pay_1"
    )

    assert outcome == "confirmed"
    item = store.items[("livia-raiz-vital", "new-woman")]
    assert item["reserved_qty"] == 0
    assert item["committed_qty"] == 3
    assert store.reservations["o1"]["status"] == "confirmed"
    assert "confirmed_sale" in store.movement_types()


@pytest.mark.asyncio
async def test_confirm_is_idempotent(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman", asaas_payment_id="pay_1")
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )
    conn = FakeConn(store)
    await inventory_store.confirm_sale_for_payment_conn(
        conn, tenant_id="livia-raiz-vital", payment_id="pay_1"
    )
    again = await inventory_store.confirm_sale_for_payment_conn(
        conn, tenant_id="livia-raiz-vital", payment_id="pay_1"
    )

    assert again == "already_confirmed"
    item = store.items[("livia-raiz-vital", "new-woman")]
    assert item["committed_qty"] == 3  # not doubled


@pytest.mark.asyncio
async def test_confirm_after_expiry_goes_to_manual_review(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman", asaas_payment_id="pay_1")
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )
    store.reservations["o1"]["status"] = "expired"  # TTL passed before payment

    conn = FakeConn(store)
    outcome = await inventory_store.confirm_sale_for_payment_conn(
        conn, tenant_id="livia-raiz-vital", payment_id="pay_1"
    )

    assert outcome == "manual_review"
    assert store.orders["o1"]["status"] == "manual_review"
    item = store.items[("livia-raiz-vital", "new-woman")]
    assert item["committed_qty"] == 0  # stock not promised


# ---------------------------------------------------------------------------
# Release / expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_frees_reserved_units(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman")
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=3, order_id="o1"
    )

    released = await inventory_store.release_reservation(order_id="o1", reason="payment_link_failed")

    assert released is True
    assert store.items[("livia-raiz-vital", "new-woman")]["reserved_qty"] == 0
    assert store.reservations["o1"]["status"] == "released"
    assert "released" in store.movement_types()


@pytest.mark.asyncio
async def test_release_is_noop_when_no_active_reservation(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman")
    # never reserved
    released = await inventory_store.release_reservation(order_id="o1")
    assert released is False


@pytest.mark.asyncio
async def test_release_expired_only_sweeps_past_ttl(store):
    from datetime import datetime, timedelta, timezone

    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    store.add_order("o1", "livia-raiz-vital", "new-woman")
    store.add_order("o2", "livia-raiz-vital", "new-woman")
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=2, order_id="o1"
    )
    await inventory_store.reserve_inventory(
        tenant_id="livia-raiz-vital", product_id="new-woman", quantity=1, order_id="o2"
    )
    # Push o1 into the past, keep o2 fresh.
    store.reservations["o1"]["reserved_until"] = datetime.now(timezone.utc) - timedelta(minutes=5)
    store.reservations["o2"]["reserved_until"] = datetime.now(timezone.utc) + timedelta(minutes=30)

    released = await inventory_store.release_expired(tenant_id="livia-raiz-vital")

    assert released == 1
    assert store.reservations["o1"]["status"] == "expired"
    assert store.reservations["o2"]["status"] == "active"
    assert store.items[("livia-raiz-vital", "new-woman")]["reserved_qty"] == 1  # only o2 left


# ---------------------------------------------------------------------------
# Refund / manual adjustment / status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_records_review_without_restocking(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10, committed=3)
    store.add_order("o1", "livia-raiz-vital", "new-woman", asaas_payment_id="pay_1")

    conn = FakeConn(store)
    ok = await inventory_store.mark_refund_review_conn(
        conn, tenant_id="livia-raiz-vital", payment_id="pay_1"
    )

    assert ok is True
    assert "refund_review" in store.movement_types()
    # Stock unchanged — no automatic return.
    assert store.items[("livia-raiz-vital", "new-woman")]["committed_qty"] == 3


@pytest.mark.asyncio
async def test_manual_adjustment_requires_reason(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)
    with pytest.raises(ValueError):
        await inventory_store.manual_adjustment(
            tenant_id="livia-raiz-vital", product_id="new-woman",
            on_hand_delta=-2, reason="  ", created_by="Fernando",
        )


@pytest.mark.asyncio
async def test_manual_adjustment_applies_and_audits(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10)

    ok = await inventory_store.manual_adjustment(
        tenant_id="livia-raiz-vital", product_id="new-woman",
        on_hand_delta=-2, reason="perda fisica", created_by="Fernando",
    )

    assert ok is True
    assert store.items[("livia-raiz-vital", "new-woman")]["on_hand_qty"] == 8
    movement = store.movements[-1]
    assert movement["movement_type"] == "manual_adjustment"
    assert movement["reason"] == "perda fisica"
    assert movement["created_by"] == "Fernando"


@pytest.mark.asyncio
async def test_inventory_status_reports_counters(store):
    store.add_item("livia-raiz-vital", "new-woman", on_hand=10, reserved=2, committed=3, buffer=1)

    rows = await inventory_store.inventory_status(tenant_id="livia-raiz-vital")

    assert len(rows) == 1
    row = rows[0]
    assert row["on_hand_qty"] == 10
    assert row["reserved_qty"] == 2
    assert row["committed_qty"] == 3
    assert row["safety_buffer_qty"] == 1
    assert row["available"] == 4  # 10 - 2 - 3 - 1


def test_affected_parses_command_tag():
    assert inventory_store._affected("UPDATE 1") == 1
    assert inventory_store._affected("UPDATE 0") == 0
    assert inventory_store._affected("INSERT 0 1") == 1
