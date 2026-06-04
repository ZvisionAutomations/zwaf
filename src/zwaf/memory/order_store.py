"""Order persistence helpers for checkout hardening."""
from __future__ import annotations

import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from zwaf.conversion.checkout_policy import normalize_delivery_address
from zwaf.security.pii import (
    can_encrypt_pii,
    document_last4,
    document_type,
    decrypt_pii,
    encrypt_pii,
    hash_pii,
)

logger = logging.getLogger("zwaf.memory.order_store")

_DELIVERY_FOLLOWUPS = (
    ("received_usage", 0),
    ("delivery_15d", 15),
    ("delivery_30d_coupon", 30),
)


async def create_order_draft(
    *,
    tenant_id: str,
    lead_phone: str,
    product_id: str,
    product_cfg: dict[str, Any],
    customer_name: str,
    customer_document: str,
    delivery_address: dict[str, Any],
    billing_type: str,
    total_cents: int,
    quantity: int = 1,
) -> str:
    db_url = _db_url()
    if not db_url:
        return ""
    if not can_encrypt_pii():
        logger.error("Cannot persist checkout PII without ZWAF_PII_FERNET_KEY")
        return ""

    order_id = str(uuid4())
    address = normalize_delivery_address(delivery_address)
    quantity = max(1, int(quantity or product_cfg.get("qty", 1)))
    external_id = str(product_cfg.get("product_id", product_id))
    shipping_cents = int(product_cfg.get("shipping_cents", 0) or 0)
    subtotal_cents = max(0, int(total_cents) - shipping_cents)

    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                INSERT INTO lead_profiles (
                    tenant_id, phone, full_name_encrypted, document_encrypted,
                    document_hash, document_last4, document_type, contact_status,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', NOW())
                ON CONFLICT (tenant_id, phone) DO UPDATE SET
                    full_name_encrypted = EXCLUDED.full_name_encrypted,
                    document_encrypted = EXCLUDED.document_encrypted,
                    document_hash = EXCLUDED.document_hash,
                    document_last4 = EXCLUDED.document_last4,
                    document_type = EXCLUDED.document_type,
                    contact_status = CASE
                        WHEN lead_profiles.opt_out_at IS NULL THEN 'active'
                        ELSE lead_profiles.contact_status
                    END,
                    updated_at = NOW()
                """,
                tenant_id,
                lead_phone,
                encrypt_pii(customer_name),
                encrypt_pii(customer_document),
                hash_pii(customer_document, tenant_id),
                document_last4(customer_document),
                document_type(customer_document),
            )
            await conn.execute(
                """
                INSERT INTO orders (
                    id, tenant_id, lead_phone, product_id, sku, quantity,
                    subtotal_cents, shipping_cents, discount_cents, total_cents,
                    status, billing_type
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, $9, 'checkout_ready', $10)
                """,
                order_id,
                tenant_id,
                lead_phone,
                product_id,
                external_id,
                quantity,
                subtotal_cents,
                shipping_cents,
                total_cents,
                billing_type,
            )
            await conn.execute(
                """
                INSERT INTO order_delivery_addresses (
                    order_id, recipient_name_encrypted, postal_code_encrypted,
                    street_encrypted, number_encrypted, complement_encrypted,
                    district_encrypted, city_encrypted, state_encrypted, address_hash
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                order_id,
                encrypt_pii(customer_name),
                encrypt_pii(address.get("postal_code", "")),
                encrypt_pii(address.get("street", "")),
                encrypt_pii(address.get("number", "")),
                encrypt_pii(address.get("complement", "")),
                encrypt_pii(address.get("district", "")),
                encrypt_pii(address.get("city", "")),
                encrypt_pii(address.get("state", "")),
                hash_pii("|".join(address.get(field, "") for field in sorted(address)), tenant_id),
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("order draft persistence failed: %s", exc)
        return ""

    return order_id


async def mark_order_payment_created(
    *,
    order_id: str,
    asaas_customer_id: str,
    asaas_payment_id: str,
    payment_url: str,
) -> None:
    db_url = _db_url()
    if not db_url or not order_id:
        return
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            await conn.execute(
                """
                UPDATE orders
                SET status = 'payment_link_created',
                    asaas_customer_id = $2,
                    asaas_payment_id = $3,
                    asaas_payment_url = $4,
                    updated_at = NOW()
                WHERE id = $1
                """,
                order_id,
                asaas_customer_id,
                asaas_payment_id,
                payment_url,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("order payment update failed: %s", exc)


async def get_order_shipping_context(*, order_id: str) -> dict[str, Any]:
    db_url = _db_url()
    if not db_url or not order_id:
        return {}
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    o.id, o.tenant_id, o.product_id, o.quantity, o.total_cents,
                    o.status, a.recipient_name_encrypted, a.postal_code_encrypted,
                    a.street_encrypted, a.number_encrypted, a.complement_encrypted,
                    a.district_encrypted, a.city_encrypted, a.state_encrypted,
                    lp.document_encrypted
                FROM orders o
                JOIN order_delivery_addresses a ON a.order_id = o.id
                LEFT JOIN lead_profiles lp
                    ON lp.tenant_id = o.tenant_id AND lp.phone = o.lead_phone
                WHERE o.id = $1
                """,
                order_id,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("order shipping context lookup failed: %s", exc)
        return {}

    if not row:
        return {}
    data = dict(row)
    return {
        "order_id": str(data.get("id") or ""),
        "tenant_id": data.get("tenant_id") or "",
        "product_id": data.get("product_id") or "",
        "quantity": data.get("quantity") or 1,
        "total_cents": data.get("total_cents") or 0,
        "status": data.get("status") or "",
        "customer_name": decrypt_pii(data.get("recipient_name_encrypted") or ""),
        "customer_document": decrypt_pii(data.get("document_encrypted") or ""),
        "postal_code": decrypt_pii(data.get("postal_code_encrypted") or ""),
        "street": decrypt_pii(data.get("street_encrypted") or ""),
        "number": decrypt_pii(data.get("number_encrypted") or ""),
        "complement": decrypt_pii(data.get("complement_encrypted") or ""),
        "district": decrypt_pii(data.get("district_encrypted") or ""),
        "city": decrypt_pii(data.get("city_encrypted") or ""),
        "state": decrypt_pii(data.get("state_encrypted") or ""),
    }


async def upsert_shipment(
    *,
    order_id: str,
    provider: str,
    external_shipment_id: str,
    status: str,
    tracking_code: str = "",
    event_type: str = "shipment_updated",
    event_at: datetime | None = None,
    raw_payload_redacted: dict[str, Any] | None = None,
) -> str:
    db_url = _db_url()
    if not db_url or not order_id:
        return ""
    event_at = event_at or datetime.now(timezone.utc)
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            shipment_id = await _find_shipment_id(
                conn,
                order_id=order_id,
                provider=provider,
                external_shipment_id=external_shipment_id,
            )
            if shipment_id:
                await conn.execute(
                    """
                    UPDATE shipments
                    SET external_shipment_id = COALESCE(NULLIF($2, ''), external_shipment_id),
                        tracking_code = COALESCE(NULLIF($3, ''), tracking_code),
                        status = $4,
                        posted_at = CASE WHEN $4 = 'posted' THEN COALESCE(posted_at, $5) ELSE posted_at END,
                        delivered_at = CASE WHEN $4 = 'delivered' THEN COALESCE(delivered_at, $5) ELSE delivered_at END,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    shipment_id,
                    external_shipment_id,
                    tracking_code,
                    status,
                    event_at,
                )
            else:
                shipment_id = await conn.fetchval(
                    """
                    INSERT INTO shipments (
                        order_id, provider, external_shipment_id, tracking_code,
                        status, posted_at, delivered_at
                    )
                    VALUES (
                        $1, $2, NULLIF($3, ''), NULLIF($4, ''), $5,
                        CASE WHEN $5 = 'posted' THEN $6 ELSE NULL END,
                        CASE WHEN $5 = 'delivered' THEN $6 ELSE NULL END
                    )
                    RETURNING id
                    """,
                    order_id,
                    provider,
                    external_shipment_id,
                    tracking_code,
                    status,
                    event_at,
                )
            await _insert_delivery_event(
                conn,
                shipment_id=shipment_id,
                event_type=event_type,
                event_at=event_at,
                source=provider,
                raw_payload_redacted=raw_payload_redacted or {},
            )
            if status == "delivered":
                await _schedule_delivery_followups(conn, order_id, event_at)
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("shipment persistence failed: %s", exc)
        return ""
    return str(shipment_id or "")


async def record_superfrete_tracking_event(
    *,
    tenant_id: str,
    event_id: str,
    event_type: str,
    provider_order_id: str,
    tracking_code: str = "",
    status: str = "",
    event_at: datetime | None = None,
    raw_payload_redacted: dict[str, Any] | None = None,
) -> str:
    db_url = _db_url()
    if not db_url:
        return "accepted_no_db"
    event_id = event_id or f"{event_type}:{provider_order_id}:{tracking_code}"
    event_at = event_at or datetime.now(timezone.utc)
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            shipment = await conn.fetchrow(
                """
                SELECT s.id, s.order_id
                FROM shipments s
                JOIN orders o ON o.id = s.order_id
                WHERE o.tenant_id = $1
                  AND s.provider = 'superfrete'
                  AND (
                    s.external_shipment_id = $2
                    OR ($3 <> '' AND s.tracking_code = $3)
                  )
                ORDER BY s.created_at DESC
                LIMIT 1
                """,
                tenant_id,
                provider_order_id,
                tracking_code,
            )
            if not shipment:
                return "missing_shipment"

            inserted = await conn.execute(
                """
                INSERT INTO webhook_events (
                    provider, tenant_id, event_id, event_type, payload_hash
                )
                VALUES ('superfrete', $1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                tenant_id,
                event_id,
                event_type,
                hash_pii(json.dumps(raw_payload_redacted or {}, sort_keys=True), tenant_id),
            )
            if not _inserted(inserted):
                return "accepted_duplicate"

            shipment_id = shipment["id"]
            order_id = shipment["order_id"]
            await conn.execute(
                """
                UPDATE shipments
                SET tracking_code = COALESCE(NULLIF($2, ''), tracking_code),
                    status = $3,
                    posted_at = CASE WHEN $3 = 'posted' THEN COALESCE(posted_at, $4) ELSE posted_at END,
                    delivered_at = CASE WHEN $3 = 'delivered' THEN COALESCE(delivered_at, $4) ELSE delivered_at END,
                    updated_at = NOW()
                WHERE id = $1
                """,
                shipment_id,
                tracking_code,
                status or event_type,
                event_at,
            )
            await _insert_delivery_event(
                conn,
                shipment_id=shipment_id,
                event_type=event_type,
                event_at=event_at,
                source="superfrete",
                raw_payload_redacted=raw_payload_redacted or {},
            )
            if status == "delivered":
                await _schedule_delivery_followups(conn, str(order_id), event_at)
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("SuperFrete tracking event persistence failed: %s", exc)
        return "accepted_db_error"
    return "accepted"


async def _find_shipment_id(
    conn: Any,
    *,
    order_id: str,
    provider: str,
    external_shipment_id: str,
) -> Any:
    if external_shipment_id:
        row_id = await conn.fetchval(
            """
            SELECT id FROM shipments
            WHERE provider = $1 AND external_shipment_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            provider,
            external_shipment_id,
        )
        if row_id:
            return row_id
    return await conn.fetchval(
        """
        SELECT id FROM shipments
        WHERE order_id = $1 AND provider = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        order_id,
        provider,
    )


async def _insert_delivery_event(
    conn: Any,
    *,
    shipment_id: Any,
    event_type: str,
    event_at: datetime,
    source: str,
    raw_payload_redacted: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO delivery_events (
            shipment_id, event_type, event_at, source, raw_payload_redacted
        )
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        shipment_id,
        event_type,
        event_at,
        source,
        json.dumps(raw_payload_redacted),
    )


async def _schedule_delivery_followups(conn: Any, order_id: str, delivered_at: datetime) -> None:
    for kind, days in _DELIVERY_FOLLOWUPS:
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


def _inserted(command_status: str) -> bool:
    return str(command_status).strip().endswith(" 1")


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")
