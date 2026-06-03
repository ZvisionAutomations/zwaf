"""Order persistence helpers for checkout hardening."""
from __future__ import annotations

import logging
import os
from typing import Any
from uuid import uuid4

from zwaf.conversion.checkout_policy import normalize_delivery_address
from zwaf.security.pii import (
    can_encrypt_pii,
    document_last4,
    document_type,
    encrypt_pii,
    hash_pii,
)

logger = logging.getLogger("zwaf.memory.order_store")


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
) -> str:
    db_url = _db_url()
    if not db_url:
        return ""
    if not can_encrypt_pii():
        logger.error("Cannot persist checkout PII without ZWAF_PII_FERNET_KEY")
        return ""

    order_id = str(uuid4())
    address = normalize_delivery_address(delivery_address)
    quantity = int(product_cfg.get("qty", 1))
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


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")
