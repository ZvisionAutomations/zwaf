"""Application service for SuperFrete fulfillment."""
from __future__ import annotations

import logging
from typing import Any, Optional

from zwaf.memory import order_store
from zwaf.shipping.superfrete import (
    SuperFreteClient,
    default_options,
    package_from_env,
    sender_from_env,
)

logger = logging.getLogger("zwaf.shipping.service")

_PRODUCT_NAMES = {
    "new-woman": "New Woman",
    "alpha-pulse": "Alpha Pulse",
}


async def quote_shipping(
    *,
    to_postal_code: str,
    quantity: int = 1,
    from_postal_code: str = "",
    services: str = "1,2,17",
    client: Optional[SuperFreteClient] = None,
) -> dict[str, Any]:
    sf = client or SuperFreteClient()
    sender = sender_from_env()
    origin = from_postal_code or sender.get("postal_code", "")
    return await sf.calculate(
        from_postal_code=origin,
        to_postal_code=to_postal_code,
        services=services,
        package=package_from_env(quantity),
        options=default_options(),
    )


async def create_label_for_order(
    *,
    order_id: str,
    service_id: int,
    client: Optional[SuperFreteClient] = None,
    execute_checkout: bool = True,
) -> dict[str, Any]:
    context = await order_store.get_order_shipping_context(order_id=order_id)
    if not context:
        return {"status": "missing_order"}

    quantity = int(context.get("quantity") or 1)
    volume = package_from_env(quantity)
    sf = client or SuperFreteClient()
    existing = await order_store.get_superfrete_shipment_for_order(order_id=order_id)
    provider_order_id = str(existing.get("external_shipment_id") or "")
    if existing and _shipment_has_label(existing):
        return _existing_label_response(existing)

    if not provider_order_id:
        cart = await sf.add_to_cart(
            sender=sender_from_env(),
            recipient=_recipient_from_context(context),
            service=service_id,
            products=[_product_declaration(context)],
            volume=volume,
            tag=order_id,
            url=_order_url(order_id),
        )
        provider_order_id = str(cart.get("id") or cart.get("order_id") or "")
        if not provider_order_id:
            logger.error("SuperFrete cart response without order id")
            return {"status": "cart_without_id"}

        await order_store.upsert_shipment(
            order_id=order_id,
            provider="superfrete",
            external_shipment_id=provider_order_id,
            status=str(cart.get("status") or "created"),
            tracking_code=str(cart.get("tracking_code") or cart.get("tracking") or ""),
            event_type="label_cart_created",
            raw_payload_redacted=_redact_operational_payload(cart),
        )

    if not execute_checkout:
        return {"status": "cart_exists" if existing else "cart_created", "provider_order_id": provider_order_id}

    checkout = await sf.checkout([provider_order_id])
    order_data = _checkout_order(checkout, provider_order_id)
    tracking_code = str(order_data.get("tracking") or order_data.get("tracking_code") or "")
    label_url = await _label_url(order_data, sf, provider_order_id)
    await order_store.upsert_shipment(
        order_id=order_id,
        provider="superfrete",
        external_shipment_id=provider_order_id,
        status=str(order_data.get("status") or checkout.get("purchase", {}).get("status") or "paid"),
        tracking_code=tracking_code,
        event_type="label_checkout",
        raw_payload_redacted=_redact_operational_payload(
            {
                "service_id": order_data.get("service_id"),
                "price": order_data.get("price"),
                "discount": order_data.get("discount"),
                "label_url": label_url,
                "tracking": tracking_code,
            }
        ),
    )
    return {
        "status": "label_created",
        "provider_order_id": provider_order_id,
        "tracking_code": tracking_code,
        "label_url": label_url,
    }


def _recipient_from_context(context: dict[str, Any]) -> dict[str, str]:
    return {
        "name": str(context.get("customer_name") or ""),
        "address": str(context.get("street") or ""),
        "complement": str(context.get("complement") or ""),
        "number": str(context.get("number") or ""),
        "district": str(context.get("district") or "NA"),
        "city": str(context.get("city") or ""),
        "state_abbr": str(context.get("state") or "").upper(),
        "postal_code": str(context.get("postal_code") or ""),
        "document": str(context.get("customer_document") or ""),
    }


def _product_declaration(context: dict[str, Any]) -> dict[str, Any]:
    quantity = max(1, int(context.get("quantity") or 1))
    total_cents = int(context.get("total_cents") or 0)
    unit_value = round((total_cents / quantity) / 100, 2) if total_cents else 0
    product_id = str(context.get("product_id") or "")
    return {
        "name": _PRODUCT_NAMES.get(product_id, product_id or "Produto Raiz Vital"),
        "quantity": quantity,
        "unitary_value": unit_value,
    }


def _checkout_order(checkout: dict[str, Any], provider_order_id: str) -> dict[str, Any]:
    orders = checkout.get("purchase", {}).get("orders", [])
    if isinstance(orders, list):
        for order in orders:
            if isinstance(order, dict) and str(order.get("id") or "") == provider_order_id:
                return order
        if orders and isinstance(orders[0], dict):
            return orders[0]
    return {}


async def _label_url(order_data: dict[str, Any], client: SuperFreteClient, provider_order_id: str) -> str:
    print_data = order_data.get("print") if isinstance(order_data, dict) else None
    if isinstance(print_data, dict):
        url = str(print_data.get("url") or "")
        if url:
            return url
    tag = await client.tag_print([provider_order_id])
    return str(tag.get("url") or "")


def _shipment_has_label(shipment: dict[str, Any]) -> bool:
    status = str(shipment.get("status") or "").lower()
    return bool(
        shipment.get("tracking_code")
        or status in {"paid", "generated", "posted", "delivered", "label_created"}
    )


def _existing_label_response(shipment: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "label_exists",
        "provider_order_id": str(shipment.get("external_shipment_id") or ""),
        "tracking_code": str(shipment.get("tracking_code") or ""),
        "label_url": "",
    }


def _order_url(order_id: str) -> str:
    return f"zwaf://orders/{order_id}"


def _redact_operational_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key in {"id", "order_id", "status", "service_id", "price", "discount", "label_url", "tracking"}
    }
