"""Payment Tool - integracao Asaas com closure por tenant."""
from __future__ import annotations

from datetime import date, timedelta
import logging
import os
from typing import Any, Callable, Optional

import httpx

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
        billing_type: str = "",
    ) -> str:
        """
        Gera link de pagamento via Asaas.

        Args:
            product_id: ID do produto ou SKU, ex: "new-woman", "new-woman-1"
            customer_phone: Numero do cliente com DDI, ex: 5511999990001
            customer_name: Nome do cliente, quando disponivel.
            customer_document: CPF/CNPJ do cliente, quando disponivel.
            billing_type: PIX, BOLETO ou CREDIT_CARD. Default vem do config/env.

        Returns:
            URL do link de pagamento.
        """
        asaas = _asaas_config(cfg)
        if not asaas["api_key"] or not asaas["base_url"]:
            logger.error("Asaas API key/base URL not configured")
            return "Erro ao gerar link: configuracao de pagamento incompleta."

        product_cfg = _resolve_product(products, product_id)
        resolved_billing_type = _resolve_billing_type(billing_type, cfg)
        price_cents = _resolve_price_cents(product_cfg, resolved_billing_type)
        quantity = product_cfg.get("qty", 1)
        product_slug = _product_slug(product_id)
        external_id = product_cfg.get("product_id", product_id)
        product_name = _PRODUCT_NAMES.get(product_slug, product_id)
        product_description = _PRODUCT_DESCRIPTIONS.get(product_slug, product_name)

        if not price_cents:
            logger.error("Product '%s' not configured for tenant '%s'", product_id, tenant_id)
            return "Erro ao gerar link: produto nao configurado."

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                customer_id = await _create_customer(
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
                        quantity=quantity,
                        price_cents=price_cents,
                        billing_type=resolved_billing_type,
                        due_days=int(cfg.get("due_days", 2)),
                    ),
                )
                resp.raise_for_status()
                data = resp.json()
                url = _extract_payment_url(data)
                if url:
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


def _resolve_product(products: dict[str, Any], product_id: str) -> dict[str, Any]:
    if product_id in products:
        return products[product_id]

    one_unit_key = f"{product_id}-1"
    if one_unit_key in products:
        return products[one_unit_key]

    normalized = product_id.replace("_", "-").lower()
    if normalized in products:
        return products[normalized]

    normalized_one_unit_key = f"{normalized}-1"
    return products.get(normalized_one_unit_key, {})


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


async def _create_customer(
    client: httpx.AsyncClient,
    config: dict[str, str],
    tenant_id: str,
    customer_phone: str,
    customer_name: str,
    customer_document: str,
) -> str:
    payload = {
        "name": customer_name or f"Cliente {customer_phone[-4:]}",
        "mobilePhone": customer_phone,
        "externalReference": f"{tenant_id}:{customer_phone}",
    }
    document = customer_document or config["default_customer_cpf_cnpj"]
    if document:
        payload["cpfCnpj"] = document

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
