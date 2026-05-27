"""Payment Tool - integracao Abacate Pay com closure por tenant."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger("zwaf.tools.payment")

_ABACATE_PAY_BASE = "https://api.abacatepay.com/v1"

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
    products = (payment_config or {}).get("products", {})

    async def generate_payment_link(product_id: str, customer_phone: str) -> str:
        """
        Gera link de pagamento via Abacate Pay.

        Args:
            product_id: ID do produto ou SKU, ex: "new-woman", "new-woman-1"
            customer_phone: Numero do cliente com DDI, ex: 5511999990001

        Returns:
            URL do link de pagamento Pix
        """
        api_key = os.getenv("ABACATE_PAY_KEY", "")
        if not api_key:
            logger.warning("ABACATE_PAY_KEY not configured - returning mock link")
            return f"https://pay.abacatepay.com/mock/{product_id}/{customer_phone[-4:]}"

        product_cfg = _resolve_product(products, product_id)
        price_cents = product_cfg.get("price_cents_pix")
        quantity = product_cfg.get("qty", 1)
        product_slug = _product_slug(product_id)
        external_id = product_cfg.get("product_id", product_id)
        product_name = _PRODUCT_NAMES.get(product_slug, product_id)
        return_url = os.getenv("ABACATE_PAY_RETURN_URL", "")
        completion_url = os.getenv("ABACATE_PAY_COMPLETION_URL", "")

        if not price_cents:
            logger.error("Product '%s' not configured for tenant '%s'", product_id, tenant_id)
            return "Erro ao gerar link: produto nao configurado."
        if not return_url or not completion_url:
            logger.error("Abacate Pay return/completion URLs not configured")
            return "Erro ao gerar link: configuracao de pagamento incompleta."

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{_ABACATE_PAY_BASE}/billing/create",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "frequency": "ONE_TIME",
                        "methods": ["PIX"],
                        "products": [
                            {
                                "externalId": external_id,
                                "name": product_name,
                                "description": _PRODUCT_DESCRIPTIONS.get(product_slug, product_name),
                                "quantity": quantity,
                                "price": price_cents,
                            }
                        ],
                        "returnUrl": return_url,
                        "completionUrl": completion_url,
                        "customer": {"cellphone": customer_phone},
                        "externalId": f"{tenant_id}-{customer_phone[-4:]}-{product_id}",
                        "metadata": {"tenant_id": tenant_id, "product_id": product_id},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                url = data.get("data", {}).get("url", "")
                if url:
                    return url
                logger.error("Abacate Pay returned no URL: %s", data)
                return "Erro ao gerar link. Tente novamente em instantes."
        except httpx.HTTPStatusError as e:
            logger.error("Abacate Pay HTTP error %s: %s", e.response.status_code, e.response.text[:200])
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
        Verifica o status de um pagamento via Abacate Pay.

        Args:
            payment_id: ID do pagamento retornado pelo Abacate Pay

        Returns:
            Status do pagamento: PAID, PENDING, EXPIRED ou mensagem de erro
        """
        api_key = os.getenv("ABACATE_PAY_KEY", "")
        if not api_key:
            return "UNKNOWN (ABACATE_PAY_KEY nao configurado)"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_ABACATE_PAY_BASE}/billing/{payment_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("data", {}).get("status", "UNKNOWN")
                return status
        except Exception as e:
            logger.error("Payment status check failed: %s", e)
            return "Nao foi possivel verificar o status do pagamento."

    check_payment_status.__name__ = "check_payment_status"
    return check_payment_status
