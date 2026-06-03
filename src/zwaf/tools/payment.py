"""Payment Tool - integracao Asaas com closure por tenant."""
from __future__ import annotations

from datetime import date, timedelta
import logging
import os
import re
from typing import Any, Callable, Optional

import httpx

from zwaf.conversion.checkout_policy import normalize_delivery_address, validate_checkout_ready
from zwaf.memory.order_store import create_order_draft, mark_order_payment_created

logger = logging.getLogger("zwaf.tools.payment")

_PRODUCT_NAMES = {
    "new-woman": "New Woman",
    "alpha-pulse": "Alpha Pulse",
}

_PRODUCT_DESCRIPTIONS = {
    "new-woman": "60 capsulas de 1450mg com oleo de linhaca, primula, borragem e vitamina E.",
    "alpha-pulse": "Frasco 30mL, 30 porcoes de 17 gotas.",
}


def make_payment_link_generator(
    tenant_id: str,
    payment_config: Optional[dict[str, Any]] = None,
) -> Callable:
    """
    Factory: retorna uma funcao de geracao de link de pagamento pre-configurada para o tenant.
    """
    cfg = payment_config or {}
    products = cfg.get("products", {})

    async def generate_payment_link(
        product_id: str,
        customer_phone: str,
        customer_name: str = "",
        customer_document: str = "",
        delivery_address: Optional[dict[str, Any]] = None,
        billing_type: str = "",
        quantity: int = 0,
    ) -> str:
        """
        Gera link de pagamento via Asaas.

        Args:
            product_id: ID do produto ou SKU, ex: "new-woman", "new-woman-1"
            customer_phone: Numero do cliente com DDI, ex: 5511999990001
            customer_name: Nome do cliente, quando disponivel.
            customer_document: CPF/CNPJ do cliente, quando disponivel.
            delivery_address: Endereco estruturado para entrega.
            billing_type: PIX, BOLETO ou CREDIT_CARD. Default vem do config/env.
            quantity: Numero de unidades. Usado no pricing tiered (preco por faixa
                de quantidade). Se 0, deriva do SKU legado ou do config.

        Returns:
            URL do link de pagamento.
        """
        asaas = _asaas_config(cfg)
        if not asaas["api_key"] or not asaas["base_url"]:
            logger.error("Asaas API key/base URL not configured")
            return "Erro ao gerar link: configuracao de pagamento incompleta."

        product_cfg, resolved_qty = _resolve_product_and_qty(products, product_id, quantity)
        resolved_billing_type = _resolve_billing_type(billing_type, cfg)
        price_cents = _total_cents(product_cfg, resolved_qty, resolved_billing_type)
        product_slug = _product_slug(product_id)
        external_id = product_cfg.get("product_id", product_id)
        product_name = _PRODUCT_NAMES.get(product_slug, product_id)
        product_description = _PRODUCT_DESCRIPTIONS.get(product_slug, product_name)

        if not price_cents:
            logger.error("Product '%s' not configured for tenant '%s'", product_id, tenant_id)
            return "Erro ao gerar link: produto nao configurado."

        checkout = validate_checkout_ready(
            tenant_id=tenant_id,
            product_id=product_id,
            customer_name=customer_name,
            customer_document=customer_document,
            delivery_address=delivery_address,
        )
        if not checkout.ok:
            if checkout.code == "blocked_product":
                return "Nao vou gerar link para esse produto neste atendimento."
            return "Erro ao gerar link: dados obrigatorios do pedido incompletos."

        address = normalize_delivery_address(delivery_address)
        order_id = await create_order_draft(
            tenant_id=tenant_id,
            lead_phone=customer_phone,
            product_id=product_id,
            product_cfg=product_cfg,
            customer_name=customer_name,
            customer_document=customer_document,
            delivery_address=address,
            billing_type=resolved_billing_type,
            total_cents=price_cents,
            quantity=resolved_qty,
        )
        if _db_required_for_checkout() and not order_id:
            return "Erro ao gerar link: pedido nao foi registrado com seguranca."

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                customer_id = await _create_or_reuse_customer(
                    client=client,
                    config=asaas,
                    tenant_id=tenant_id,
                    customer_phone=customer_phone,
                    customer_name=customer_name,
                    customer_document=customer_document,
                )
                if not customer_id:
                    return "Erro ao gerar link de pagamento. Por favor, tente novamente."

                resp = await client.post(
                    f"{asaas['base_url']}/payments",
                    headers=_asaas_headers(asaas),
                    json=_payment_payload(
                        tenant_id=tenant_id,
                        customer_id=customer_id,
                        customer_phone=customer_phone,
                        product_id=product_id,
                        external_id=external_id,
                        product_name=product_name,
                        product_description=product_description,
                        quantity=resolved_qty,
                        price_cents=price_cents,
                        billing_type=resolved_billing_type,
                        due_days=int(cfg.get("due_days", 2)),
                    ),
                )
                resp.raise_for_status()
                data = resp.json()
                url = _extract_payment_url(data)
                if url:
                    await mark_order_payment_created(
                        order_id=order_id,
                        asaas_customer_id=customer_id,
                        asaas_payment_id=str(data.get("id", "")),
                        payment_url=url,
                    )
                    return url
                logger.error("Asaas returned no payment URL: %s", _redact(data))
                return "Erro ao gerar link. Tente novamente em instantes."
        except httpx.HTTPStatusError as e:
            logger.error("Asaas HTTP error %s: %s", e.response.status_code, e.response.text[:200])
            return "Erro ao gerar link de pagamento. Por favor, tente novamente."
        except Exception as e:
            logger.error("Payment link generation failed: %s", e)
            return "Erro ao gerar link de pagamento. Por favor, tente novamente."

    generate_payment_link.__name__ = "generate_payment_link"
    return generate_payment_link


def _split_product_qty(product_id: str) -> tuple[str, Optional[int]]:
    """Split a SKU like 'new-woman-2' into ('new-woman', 2). No numeric suffix -> (slug, None)."""
    normalized = (product_id or "").replace("_", "-").lower()
    match = re.match(r"^(.*?)-(\d+)$", normalized)
    if match:
        return match.group(1), int(match.group(2))
    return normalized, None


def _resolve_product_and_qty(
    products: dict[str, Any],
    product_id: str,
    quantity: int,
) -> tuple[dict[str, Any], int]:
    """Resolve product config and quantity for both tiered and legacy package formats."""
    slug, suffix_qty = _split_product_qty(product_id)
    cfg = (
        products.get(product_id)
        or products.get(slug)
        or products.get(f"{slug}-1")
        or {}
    )
    if quantity and int(quantity) > 0:
        resolved_qty = int(quantity)
    elif suffix_qty:
        resolved_qty = suffix_qty
    else:
        resolved_qty = int(cfg.get("qty", 1))
    return cfg, max(1, resolved_qty)


def _tier_unit_cents(tiers: list[dict[str, Any]], qty: int) -> Optional[int]:
    """Return the Pix unit price (cents) for the tier matching qty."""
    for tier in tiers:
        min_qty = int(tier.get("min_qty", 1))
        max_qty = tier.get("max_qty")
        if qty >= min_qty and (max_qty is None or qty <= int(max_qty)):
            unit = tier.get("unit_cents")
            return int(unit) if unit else None
    return None


def _total_cents(product_cfg: dict[str, Any], qty: int, billing_type: str) -> Optional[int]:
    """Order total in cents: tiered unit pricing when configured, else legacy package price."""
    tiers = product_cfg.get("unit_price_tiers_pix_cents")
    if tiers:
        unit_pix = _tier_unit_cents(tiers, qty)
        if not unit_pix:
            return None
        if billing_type == "CREDIT_CARD":
            markup_pct = product_cfg.get("card_markup_pct", 0) or 0
            unit = int(round(unit_pix * (100 + markup_pct) / 100))
        else:
            unit = int(unit_pix)
        return unit * qty
    return _resolve_price_cents(product_cfg, billing_type)


def _asaas_config(payment_config: dict[str, Any]) -> dict[str, str]:
    base_url = _config_value(payment_config, "base_url", "ASAAS_BASE_URL")
    return {
        "api_key": _config_value(payment_config, "api_key", "ASAAS_API_KEY"),
        "base_url": base_url.rstrip("/"),
        "user_agent": _config_value(payment_config, "user_agent", "ASAAS_USER_AGENT", "zwaf-raiz-vital"),
        "default_customer_cpf_cnpj": _config_value(
            payment_config,
            "default_customer_cpf_cnpj",
            "ASAAS_DEFAULT_CUSTOMER_CPF_CNPJ",
        ),
    }


def _db_required_for_checkout() -> bool:
    if os.getenv("DATABASE_URL", ""):
        return True
    return os.getenv("ZWAF_REQUIRE_ORDER_PERSISTENCE", "").lower() in {"1", "true", "yes"}


def _config_value(
    payment_config: dict[str, Any],
    key: str,
    env_key: str,
    default: str = "",
) -> str:
    value = payment_config.get(key) or os.getenv(env_key, default)
    return str(value).strip()


def _asaas_headers(config: dict[str, str]) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": config["user_agent"],
        "access_token": config["api_key"],
    }


def _customer_external_reference(tenant_id: str, customer_phone: str) -> str:
    return f"{tenant_id}:{customer_phone}"


async def _find_customer_by_external_reference(
    client: httpx.AsyncClient,
    config: dict[str, str],
    external_reference: str,
) -> dict[str, Any]:
    resp = await client.get(
        f"{config['base_url']}/customers",
        headers=_asaas_headers(config),
        params={"externalReference": external_reference, "limit": 1},
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first
    return {}


async def _update_customer_document(
    client: httpx.AsyncClient,
    config: dict[str, str],
    customer_id: str,
    customer_phone: str,
    customer_name: str,
    customer_document: str,
) -> str:
    payload = {
        "name": customer_name or f"Cliente {customer_phone[-4:]}",
        "mobilePhone": customer_phone,
        "cpfCnpj": customer_document,
    }
    resp = await client.put(
        f"{config['base_url']}/customers/{customer_id}",
        headers=_asaas_headers(config),
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("id") or customer_id)


async def _create_or_reuse_customer(
    client: httpx.AsyncClient,
    config: dict[str, str],
    tenant_id: str,
    customer_phone: str,
    customer_name: str,
    customer_document: str,
) -> str:
    external_reference = _customer_external_reference(tenant_id, customer_phone)
    document = customer_document
    existing = await _find_customer_by_external_reference(client, config, external_reference)
    if existing:
        customer_id = str(existing.get("id", ""))
        if customer_id and document and existing.get("cpfCnpj") != document:
            return await _update_customer_document(
                client=client,
                config=config,
                customer_id=customer_id,
                customer_phone=customer_phone,
                customer_name=customer_name,
                customer_document=document,
            )
        return customer_id

    if not document:
        logger.error("Asaas customer document missing for tenant '%s'", tenant_id)
        return ""

    payload = {
        "name": customer_name or f"Cliente {customer_phone[-4:]}",
        "mobilePhone": customer_phone,
        "externalReference": external_reference,
        "cpfCnpj": document,
    }

    resp = await client.post(
        f"{config['base_url']}/customers",
        headers=_asaas_headers(config),
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    customer_id = data.get("id", "")
    if not customer_id:
        logger.error("Asaas customer response without id: %s", _redact(data))
    return customer_id


def _payment_payload(
    tenant_id: str,
    customer_id: str,
    customer_phone: str,
    product_id: str,
    external_id: str,
    product_name: str,
    product_description: str,
    quantity: int,
    price_cents: int,
    billing_type: str,
    due_days: int,
) -> dict[str, Any]:
    due_date = date.today() + timedelta(days=max(0, due_days))
    return {
        "customer": customer_id,
        "billingType": billing_type,
        "value": round(price_cents / 100, 2),
        "dueDate": due_date.isoformat(),
        "description": f"{product_name} ({quantity} un.) - {product_description}",
        "externalReference": f"{tenant_id}:{customer_phone}:{product_id}:{external_id}",
    }


def _resolve_billing_type(requested: str, payment_config: dict[str, Any]) -> str:
    billing_type = (requested or payment_config.get("billing_type") or "PIX").upper()
    if billing_type not in {"PIX", "BOLETO", "CREDIT_CARD"}:
        logger.warning("Unsupported Asaas billing type '%s'; falling back to PIX", billing_type)
        return "PIX"
    return billing_type


def _resolve_price_cents(product_cfg: dict[str, Any], billing_type: str) -> Optional[int]:
    if billing_type == "CREDIT_CARD":
        return product_cfg.get("price_cents_card") or product_cfg.get("price_cents_pix")
    if billing_type == "BOLETO":
        return product_cfg.get("price_cents_boleto") or product_cfg.get("price_cents_pix")
    return product_cfg.get("price_cents_pix")


def _extract_payment_url(data: dict[str, Any]) -> str:
    for key in ("invoiceUrl", "bankSlipUrl", "url", "transactionReceiptUrl"):
        value = data.get(key)
        if value:
            return str(value)
    return ""


def _redact(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: "***" if key.lower() in {"access_token", "api_key", "token"} else _redact(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


def _product_slug(product_id: str) -> str:
    normalized = product_id.replace("_", "-").lower()
    for slug in _PRODUCT_NAMES:
        if normalized.startswith(slug):
            return slug
    return normalized


def make_payment_status_checker() -> Callable:
    """Factory: retorna funcao de verificacao de status de pagamento."""

    async def check_payment_status(payment_id: str) -> str:
        """
        Verifica o status de um pagamento via Asaas.

        Args:
            payment_id: ID do pagamento retornado pelo Asaas

        Returns:
            Status do pagamento retornado pelo Asaas ou mensagem de erro.
        """
        config = _asaas_config({})
        if not config["api_key"] or not config["base_url"]:
            return "UNKNOWN (ASAAS_API_KEY/ASAAS_BASE_URL nao configurado)"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{config['base_url']}/payments/{payment_id}",
                    headers=_asaas_headers(config),
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "UNKNOWN")
                return status
        except Exception as e:
            logger.error("Payment status check failed: %s", e)
            return "Nao foi possivel verificar o status do pagamento."

    check_payment_status.__name__ = "check_payment_status"
    return check_payment_status
