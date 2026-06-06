"""Transactional inventory store for oversell-safe checkout (story-034).

Stock is decided by the backend, never by the agent prompt. The reservation is
an atomic ``UPDATE ... WHERE available >= qty`` inside a Postgres transaction:
the row lock makes two concurrent checkouts for the last unit serialize, so
``available`` can never go negative.

Connection model mirrors :mod:`zwaf.memory.order_store`: each public entry point
opens its own ``asyncpg`` connection, while ``_conn``-suffixed helpers operate
inside a caller-provided connection so the webhook can confirm/release stock in
the same transaction that records the payment event.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("zwaf.memory.inventory_store")

DEFAULT_TTL_MINUTES = 30

# available = on_hand_qty - reserved_qty - committed_qty - safety_buffer_qty
_AVAILABLE_EXPR = (
    "on_hand_qty - reserved_qty - committed_qty - safety_buffer_qty"
)


@dataclass(frozen=True)
class ReservationResult:
    """Outcome of a reservation attempt.

    ``ok`` means the checkout may proceed to Asaas. ``status`` disambiguates:
    ``reserved`` (fresh hold), ``already_reserved`` (idempotent retry),
    ``skipped_no_db`` (no DATABASE_URL — dev/test), ``unavailable`` (not enough
    stock — do NOT call Asaas), ``error`` (infra failure — do NOT call Asaas).
    """

    ok: bool
    status: str
    reservation_id: str = ""
    available: Optional[int] = None


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")


def _affected(command_status: str) -> int:
    """Parse the row count from an asyncpg command tag like 'UPDATE 1'."""
    try:
        return int(str(command_status).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Reservation (checkout)
# ---------------------------------------------------------------------------


async def reserve_inventory(
    *,
    tenant_id: str,
    product_id: str,
    quantity: int,
    order_id: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    idempotency_key: Optional[str] = None,
) -> ReservationResult:
    """Atomically reserve ``quantity`` units for ``order_id`` before payment.

    Returns a :class:`ReservationResult`. When no database is configured the
    reservation is skipped (ok) so the in-memory/dev checkout still works.
    """
    db_url = _db_url()
    if not db_url:
        return ReservationResult(ok=True, status="skipped_no_db")

    quantity = int(quantity)
    if quantity <= 0:
        return ReservationResult(ok=False, status="error")

    key = idempotency_key or order_id
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            async with conn.transaction():
                return await _reserve_conn(
                    conn,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    quantity=quantity,
                    order_id=order_id,
                    ttl_minutes=ttl_minutes,
                    idempotency_key=key,
                )
        finally:
            await conn.close()
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.error("inventory reservation failed: %s", exc)
        return ReservationResult(ok=False, status="error")


async def _reserve_conn(
    conn: Any,
    *,
    tenant_id: str,
    product_id: str,
    quantity: int,
    order_id: str,
    ttl_minutes: int,
    idempotency_key: str,
) -> ReservationResult:
    # Idempotency: a reservation already exists for this order -> do not double
    # count. An active/confirmed reservation lets checkout proceed.
    existing = await conn.fetchrow(
        "SELECT id, status FROM inventory_reservations WHERE order_id = $1",
        order_id,
    )
    if existing:
        ok = existing["status"] in {"active", "confirmed"}
        return ReservationResult(
            ok=ok,
            status="already_reserved" if ok else "unavailable",
            reservation_id=str(existing["id"]),
        )

    # Conditional reserve. The WHERE clause is the concurrency gate: the row lock
    # held until commit forces concurrent checkouts to re-evaluate availability.
    update_status = await conn.execute(
        f"""
        UPDATE inventory_items
        SET reserved_qty = reserved_qty + $3,
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = $1
          AND product_id = $2
          AND {_AVAILABLE_EXPR} >= $3
        """,
        tenant_id,
        product_id,
        quantity,
    )
    if _affected(update_status) == 0:
        return ReservationResult(ok=False, status="unavailable")

    reserved_until = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    reservation_id = await conn.fetchval(
        """
        INSERT INTO inventory_reservations (
            order_id, tenant_id, product_id, quantity,
            status, reserved_until, idempotency_key
        )
        VALUES ($1, $2, $3, $4, 'active', $5, $6)
        RETURNING id
        """,
        order_id,
        tenant_id,
        product_id,
        quantity,
        reserved_until,
        idempotency_key,
    )
    await _record_movement(
        conn,
        tenant_id=tenant_id,
        product_id=product_id,
        order_id=order_id,
        reservation_id=str(reservation_id),
        movement_type="reserved",
        quantity_delta=quantity,
        reason="checkout reservation",
    )
    await conn.execute(
        """
        UPDATE orders
        SET status = 'stock_reserved', updated_at = NOW()
        WHERE id = $1 AND status NOT IN ('paid', 'stock_confirmed')
        """,
        order_id,
    )
    return ReservationResult(
        ok=True, status="reserved", reservation_id=str(reservation_id)
    )


# ---------------------------------------------------------------------------
# Release (Asaas failure / cancellation before payment)
# ---------------------------------------------------------------------------


async def release_reservation(
    *,
    order_id: str,
    reason: str = "released",
    created_by: str = "system",
) -> bool:
    """Release an active reservation for ``order_id`` (idempotent)."""
    db_url = _db_url()
    if not db_url or not order_id:
        return False
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            async with conn.transaction():
                return await release_reservation_conn(
                    conn,
                    order_id=order_id,
                    movement_type="released",
                    new_status="released",
                    reason=reason,
                    created_by=created_by,
                )
        finally:
            await conn.close()
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.error("inventory release failed: %s", exc)
        return False


async def release_reservation_conn(
    conn: Any,
    *,
    order_id: str,
    movement_type: str,
    new_status: str,
    reason: str,
    created_by: str = "system",
) -> bool:
    """Free reserved units for an active reservation, within ``conn``'s tx.

    Only an ``active`` reservation is touched, so repeated calls are no-ops
    (idempotent). Returns True when a reservation was actually released.
    """
    reservation = await conn.fetchrow(
        """
        SELECT id, tenant_id, product_id, quantity
        FROM inventory_reservations
        WHERE order_id = $1 AND status = 'active'
        FOR UPDATE
        """,
        order_id,
    )
    if not reservation:
        return False

    await conn.execute(
        """
        UPDATE inventory_items
        SET reserved_qty = reserved_qty - $3,
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = $1 AND product_id = $2
        """,
        reservation["tenant_id"],
        reservation["product_id"],
        reservation["quantity"],
    )
    await conn.execute(
        "UPDATE inventory_reservations SET status = $2, updated_at = NOW() WHERE id = $1",
        reservation["id"],
        new_status,
    )
    await _record_movement(
        conn,
        tenant_id=reservation["tenant_id"],
        product_id=reservation["product_id"],
        order_id=order_id,
        reservation_id=str(reservation["id"]),
        movement_type=movement_type,
        quantity_delta=-int(reservation["quantity"]),
        reason=reason,
        created_by=created_by,
    )
    return True


# ---------------------------------------------------------------------------
# Webhook confirmation (payment received)
# ---------------------------------------------------------------------------


async def confirm_sale_for_payment_conn(
    conn: Any,
    *,
    tenant_id: str,
    payment_id: str,
) -> str:
    """Confirm the reservation tied to ``payment_id`` (within ``conn``'s tx).

    Returns one of: ``confirmed`` (reserved -> committed), ``already_confirmed``
    (idempotent no-op), ``manual_review`` (paid after the hold expired/was
    released — stock not promised), ``no_reservation`` (order had none).
    """
    if not payment_id:
        return "no_reservation"

    reservation = await conn.fetchrow(
        """
        SELECT r.id, r.order_id, r.tenant_id, r.product_id, r.quantity, r.status
        FROM inventory_reservations r
        JOIN orders o ON o.id = r.order_id
        WHERE o.tenant_id = $1 AND o.asaas_payment_id = $2
        ORDER BY r.created_at DESC
        LIMIT 1
        FOR UPDATE OF r
        """,
        tenant_id,
        payment_id,
    )
    if not reservation:
        return "no_reservation"

    status = reservation["status"]
    if status == "confirmed":
        return "already_confirmed"

    if status != "active":
        # Paid after the hold expired or was released. Do NOT touch stock and do
        # NOT promise shipment — flag the order for a human.
        await conn.execute(
            """
            UPDATE orders
            SET status = 'manual_review', updated_at = NOW()
            WHERE id = $1
            """,
            reservation["order_id"],
        )
        await _record_movement(
            conn,
            tenant_id=reservation["tenant_id"],
            product_id=reservation["product_id"],
            order_id=str(reservation["order_id"]),
            reservation_id=str(reservation["id"]),
            movement_type="refund_review",
            quantity_delta=0,
            reason=f"payment after reservation {status}; manual review",
        )
        return "manual_review"

    quantity = int(reservation["quantity"])
    await conn.execute(
        """
        UPDATE inventory_items
        SET reserved_qty = reserved_qty - $3,
            committed_qty = committed_qty + $3,
            version = version + 1,
            updated_at = NOW()
        WHERE tenant_id = $1 AND product_id = $2
        """,
        reservation["tenant_id"],
        reservation["product_id"],
        quantity,
    )
    await conn.execute(
        "UPDATE inventory_reservations SET status = 'confirmed', updated_at = NOW() WHERE id = $1",
        reservation["id"],
    )
    await _record_movement(
        conn,
        tenant_id=reservation["tenant_id"],
        product_id=reservation["product_id"],
        order_id=str(reservation["order_id"]),
        reservation_id=str(reservation["id"]),
        movement_type="confirmed_sale",
        quantity_delta=quantity,
        reason="payment confirmed",
    )
    return "confirmed"


async def release_reservation_for_payment_conn(
    conn: Any,
    *,
    tenant_id: str,
    payment_id: str,
    reason: str = "cancelled before payment",
) -> bool:
    """Release the active reservation for ``payment_id`` (within ``conn``'s tx)."""
    if not payment_id:
        return False
    order = await conn.fetchrow(
        "SELECT id FROM orders WHERE tenant_id = $1 AND asaas_payment_id = $2 LIMIT 1",
        tenant_id,
        payment_id,
    )
    if not order:
        return False
    return await release_reservation_conn(
        conn,
        order_id=str(order["id"]),
        movement_type="released",
        new_status="released",
        reason=reason,
    )


async def mark_refund_review_conn(
    conn: Any,
    *,
    tenant_id: str,
    payment_id: str,
) -> bool:
    """Record a refund as needing human review (within ``conn``'s tx).

    Stock is never returned automatically — the unit may already be shipped.
    """
    if not payment_id:
        return False
    order = await conn.fetchrow(
        """
        SELECT id, product_id FROM orders
        WHERE tenant_id = $1 AND asaas_payment_id = $2
        LIMIT 1
        """,
        tenant_id,
        payment_id,
    )
    if not order:
        return False
    await _record_movement(
        conn,
        tenant_id=tenant_id,
        product_id=order["product_id"],
        order_id=str(order["id"]),
        reservation_id=None,
        movement_type="refund_review",
        quantity_delta=0,
        reason="refund received; human review before restocking",
    )
    return True


# ---------------------------------------------------------------------------
# Expiry sweep (CLI)
# ---------------------------------------------------------------------------


async def release_expired(
    *,
    tenant_id: str,
    now: Optional[datetime] = None,
) -> int:
    """Release every active reservation past its TTL. Idempotent.

    Returns the number of reservations expired in this run.
    """
    db_url = _db_url()
    if not db_url:
        return 0
    cutoff = now or datetime.now(timezone.utc)
    released = 0
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            rows = await conn.fetch(
                """
                SELECT order_id FROM inventory_reservations
                WHERE tenant_id = $1 AND status = 'active' AND reserved_until < $2
                """,
                tenant_id,
                cutoff,
            )
            for row in rows:
                async with conn.transaction():
                    did = await release_reservation_conn(
                        conn,
                        order_id=str(row["order_id"]),
                        movement_type="expired",
                        new_status="expired",
                        reason="reservation TTL expired",
                    )
                if did:
                    released += 1
        finally:
            await conn.close()
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.error("release_expired failed: %s", exc)
        return released
    return released


# ---------------------------------------------------------------------------
# Status (CLI)
# ---------------------------------------------------------------------------


async def inventory_status(
    *,
    tenant_id: str,
    product_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return stock counters per product for a tenant."""
    db_url = _db_url()
    if not db_url:
        return []
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            if product_id:
                rows = await conn.fetch(
                    f"""
                    SELECT tenant_id, product_id, on_hand_qty, reserved_qty,
                           committed_qty, safety_buffer_qty,
                           ({_AVAILABLE_EXPR}) AS available
                    FROM inventory_items
                    WHERE tenant_id = $1 AND product_id = $2
                    """,
                    tenant_id,
                    product_id,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT tenant_id, product_id, on_hand_qty, reserved_qty,
                           committed_qty, safety_buffer_qty,
                           ({_AVAILABLE_EXPR}) AS available
                    FROM inventory_items
                    WHERE tenant_id = $1
                    ORDER BY product_id
                    """,
                    tenant_id,
                )
        finally:
            await conn.close()
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.error("inventory_status failed: %s", exc)
        return []
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Manual adjustment (operator reconciliation)
# ---------------------------------------------------------------------------


async def manual_adjustment(
    *,
    tenant_id: str,
    product_id: str,
    on_hand_delta: int,
    reason: str,
    created_by: str,
) -> bool:
    """Apply an audited correction to on-hand stock. ``reason`` is mandatory.

    Preserves history: only ``on_hand_qty`` changes, reservations and the
    movements ledger stay intact. The DB no-oversell constraint rejects a
    reduction that would drop below already reserved/committed units.
    """
    if not reason or not reason.strip():
        raise ValueError("manual_adjustment requires a non-empty reason")
    if not created_by or not created_by.strip():
        raise ValueError("manual_adjustment requires created_by")

    db_url = _db_url()
    if not db_url:
        return False
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            async with conn.transaction():
                status = await conn.execute(
                    """
                    UPDATE inventory_items
                    SET on_hand_qty = on_hand_qty + $3,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = $1 AND product_id = $2
                    """,
                    tenant_id,
                    product_id,
                    int(on_hand_delta),
                )
                if _affected(status) == 0:
                    return False
                await _record_movement(
                    conn,
                    tenant_id=tenant_id,
                    product_id=product_id,
                    order_id=None,
                    reservation_id=None,
                    movement_type="manual_adjustment",
                    quantity_delta=int(on_hand_delta),
                    reason=reason.strip(),
                    created_by=created_by.strip(),
                )
        finally:
            await conn.close()
    except ValueError:
        raise
    except Exception as exc:  # pragma: no cover - infra failure path
        logger.error("manual_adjustment failed: %s", exc)
        return False
    return True


# ---------------------------------------------------------------------------
# Ledger helper
# ---------------------------------------------------------------------------


async def _record_movement(
    conn: Any,
    *,
    tenant_id: str,
    product_id: str,
    order_id: Optional[str],
    reservation_id: Optional[str],
    movement_type: str,
    quantity_delta: int,
    reason: str,
    created_by: str = "system",
) -> None:
    await conn.execute(
        """
        INSERT INTO inventory_movements (
            tenant_id, product_id, order_id, reservation_id,
            movement_type, quantity_delta, reason, created_by
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        tenant_id,
        product_id,
        order_id,
        reservation_id,
        movement_type,
        int(quantity_delta),
        reason,
        created_by,
    )
