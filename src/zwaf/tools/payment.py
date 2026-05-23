"""Payment Tool — integração Abacate Pay para geração e verificação de links."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

from .base import BaseTool, ToolResult

logger = logging.getLogger("zwaf.tools.payment")

_ABACATE_PAY_BASE = "https://api.abacatepay.com/v1"


async def generate_payment_link(
    product_id: str,
    customer_phone: str,
    tenant_id: Optional[str] = None,
) -> str:
    """
    Gera link de pagamento via Abacate Pay.

    Args:
        product_id: ID do produto (ex: "nw-001" para New Woman)
        customer_phone: Número do cliente
        tenant_id: ID do tenant para lookup de api_key e produto

    Returns:
        URL do link de pagamento ou mensagem de erro
    """
    api_key = os.getenv("ABACATE_PAY_KEY", "")
    if not api_key:
        logger.warning("ABACATE_PAY_KEY not configured — returning mock link")
        return f"https://pay.abacatepay.com/mock/{product_id}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_ABACATE_PAY_BASE}/billing/create",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "frequency": "ONE_TIME",
                    "methods": ["PIX"],
                    "products": [{"externalId": product_id, "quantity": 1}],
                    "customer": {"cellphone": customer_phone},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            url = data.get("data", {}).get("url", "")
            return url if url else f"Link gerado: {data}"
    except Exception as e:
        logger.error("Payment link generation failed: %s", e)
        return f"Erro ao gerar link de pagamento. Por favor, tente novamente."


async def check_payment_status(payment_id: str) -> str:
    """
    Verifica status de um pagamento via Abacate Pay.

    Returns:
        "PAID", "PENDING", "EXPIRED" ou mensagem de erro
    """
    api_key = os.getenv("ABACATE_PAY_KEY", "")
    if not api_key:
        return "UNKNOWN (ABACATE_PAY_KEY not configured)"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_ABACATE_PAY_BASE}/billing/{payment_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("status", "UNKNOWN")
    except Exception as e:
        logger.error("Payment status check failed: %s", e)
        return "Não foi possível verificar o status do pagamento."
