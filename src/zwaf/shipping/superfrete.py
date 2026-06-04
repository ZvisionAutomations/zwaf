"""SuperFrete API client.

Docs consulted via Context7 on 2026-06-03:
- Bearer token + User-Agent headers.
- POST /api/v0/calculator for freight quotes.
- POST /api/v0/cart then POST /api/v0/checkout for label purchase/tracking.
- POST /api/v0/tag/print for PDF label URL.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from urllib.parse import urlencode
from typing import Any, Optional

import httpx

logger = logging.getLogger("zwaf.shipping.superfrete")


@dataclass(frozen=True)
class SuperFreteConfig:
    token: str
    base_url: str = "https://sandbox.superfrete.com"
    user_agent: str = "ZWAF Raiz Vital/1.0 (tecnico@seu-dominio.com.br)"
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "SuperFreteConfig":
        return cls(
            token=os.getenv("SUPERFRETE_TOKEN", "").strip(),
            base_url=os.getenv("SUPERFRETE_BASE_URL", "https://sandbox.superfrete.com").strip(),
            user_agent=os.getenv(
                "SUPERFRETE_USER_AGENT",
                "ZWAF Raiz Vital/1.0 (tecnico@seu-dominio.com.br)",
            ).strip(),
            timeout_seconds=float(os.getenv("SUPERFRETE_TIMEOUT_SECONDS", "10") or 10),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.base_url and valid_user_agent(self.user_agent))


class SuperFreteClient:
    def __init__(
        self,
        config: Optional[SuperFreteConfig] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config or SuperFreteConfig.from_env()
        self._client = client

    async def calculate(
        self,
        *,
        from_postal_code: str,
        to_postal_code: str,
        services: str,
        package: dict[str, Any],
        options: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        payload = {
            "from": {"postal_code": _digits(from_postal_code)},
            "to": {"postal_code": _digits(to_postal_code)},
            "services": services,
            "options": options or default_options(),
            "package": package,
        }
        data = await self._post_json("/api/v0/calculator", payload)
        return {
            "services": normalize_quote_services(data),
            "package": data.get("package") if isinstance(data, dict) else None,
            "raw": data,
        }

    async def add_to_cart(
        self,
        *,
        sender: dict[str, Any],
        recipient: dict[str, Any],
        service: int,
        products: list[dict[str, Any]],
        volume: dict[str, Any],
        options: Optional[dict[str, Any]] = None,
        platform: str = "ZWAF",
        tag: str = "",
        url: str = "",
    ) -> dict[str, Any]:
        payload = {
            "from": sender,
            "to": recipient,
            "service": int(service),
            "products": products,
            "volumes": volume,
            "platform": platform,
        }
        if tag:
            payload["tag"] = tag
        if url:
            payload["url"] = url
        payload["options"] = options or default_options(non_commercial=True)
        return await self._post_json("/api/v0/cart", payload)

    async def checkout(self, order_ids: list[str]) -> dict[str, Any]:
        return await self._post_json("/api/v0/checkout", {"orders": order_ids})

    async def tag_print(self, order_ids: list[str]) -> dict[str, Any]:
        return await self._post_json("/api/v0/tag/print", {"orders": order_ids})

    async def get_services_info(self) -> dict[str, Any]:
        return await self._get_json("/api/v0/services/info")

    async def get_user(self) -> dict[str, Any]:
        return await self._get_json("/api/v0/user")

    async def list_user_addresses(self) -> dict[str, Any]:
        return await self._get_json("/api/v0/user/addresses")

    async def get_order_info(self, order_id: str) -> dict[str, Any]:
        return await self._get_json(f"/api/v0/order/info/{order_id}")

    async def list_orders(self, *, page: int | None = None, limit: int | None = None) -> dict[str, Any]:
        params = {key: value for key, value in {"page": page, "limit": limit}.items() if value is not None}
        return await self._get_json("/api/v0/me/orders", params=params)

    async def cancel_order(self, order_id: str, description: str = "Cancelado pelo operador ZWAF") -> dict[str, Any]:
        return await self._post_json(
            "/api/v0/order/cancel",
            {"order": {"id": order_id, "description": description}},
        )

    async def list_webhook_apps(self) -> dict[str, Any]:
        return await self._get_json("/api/v0/webhook")

    async def create_webhook_app(
        self,
        *,
        url: str,
        event_types: list[str],
        name: str = "ZWAF SuperFrete",
        secret_token: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url, "event_types": event_types, "name": name}
        if secret_token:
            payload["secret_token"] = secret_token
        return await self._post_json("/api/v0/webhook", payload)

    async def update_webhook_app(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        event_types: list[str] | None = None,
        name: str | None = None,
        secret_token: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in {
                "url": url,
                "event_types": event_types,
                "name": name,
                "secret_token": secret_token,
            }.items()
            if value is not None
        }
        return await self._put_json(f"/api/v0/webhook/{webhook_id}", payload)

    async def delete_webhook_app(self, webhook_id: str) -> dict[str, Any]:
        return await self._delete_json(f"/api/v0/webhook/{webhook_id}")

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("POST", path, payload=payload)

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request_json("GET", path, params=params)

    async def _put_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("PUT", path, payload=payload)

    async def _delete_json(self, path: str) -> dict[str, Any]:
        return await self._request_json("DELETE", path)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise SuperFreteConfigError("SUPERFRETE_TOKEN/base URL/User-Agent not configured")

        url = self._url(path, params=params)
        if self._client is not None:
            response = await self._client.request(method, url, headers=self._headers(), json=payload)
            return _response_json(response)

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            response = await client.request(method, url, headers=self._headers(), json=payload)
            return _response_json(response)

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        if not params:
            return base
        return f"{base}?{urlencode(params)}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token}",
            "User-Agent": self.config.user_agent,
            "accept": "application/json",
            "content-type": "application/json",
        }


class SuperFreteConfigError(RuntimeError):
    """Raised when SuperFrete cannot be used because required envs are absent."""


def default_options(*, non_commercial: bool = False) -> dict[str, Any]:
    options = {
        "own_hand": False,
        "receipt": False,
        "insurance_value": 0,
        "use_insurance_value": False,
    }
    if non_commercial:
        options["non_commercial"] = True
    return options


def normalize_quote_services(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("services") or data.get("data") or []
    else:
        items = []
    if not isinstance(items, list):
        return []

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "service_id": item.get("id") or item.get("service_id") or item.get("code"),
                "name": item.get("name") or item.get("service_name") or "",
                "price": item.get("price") or item.get("final_price") or item.get("value"),
                "delivery_time": item.get("delivery_time") or item.get("delivery_days"),
                "raw": item,
            }
        )
    return normalized


def sender_from_env() -> dict[str, str]:
    return {
        "name": os.getenv("SUPERFRETE_FROM_NAME", "Loja Raiz Vital"),
        "address": os.getenv("SUPERFRETE_FROM_ADDRESS", ""),
        "complement": os.getenv("SUPERFRETE_FROM_COMPLEMENT", ""),
        "number": os.getenv("SUPERFRETE_FROM_NUMBER", ""),
        "district": os.getenv("SUPERFRETE_FROM_DISTRICT", ""),
        "city": os.getenv("SUPERFRETE_FROM_CITY", ""),
        "state_abbr": os.getenv("SUPERFRETE_FROM_STATE", "").upper(),
        "postal_code": _digits(os.getenv("SUPERFRETE_FROM_POSTAL_CODE", "")),
        "document": _digits(os.getenv("SUPERFRETE_FROM_DOCUMENT", "")),
    }


def package_from_env(quantity: int = 1) -> dict[str, float]:
    qty = max(1, int(quantity or 1))
    unit_weight = float(os.getenv("SUPERFRETE_PACKAGE_UNIT_WEIGHT_KG", "0.3") or 0.3)
    return {
        "height": float(os.getenv("SUPERFRETE_PACKAGE_HEIGHT_CM", "6") or 6),
        "width": float(os.getenv("SUPERFRETE_PACKAGE_WIDTH_CM", "16") or 16),
        "length": float(os.getenv("SUPERFRETE_PACKAGE_LENGTH_CM", "24") or 24),
        "weight": round(unit_weight * qty, 3),
    }


def _response_json(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {"data": data}


def _digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def valid_user_agent(value: str) -> bool:
    user_agent = str(value or "").strip()
    return bool(user_agent and "@" in user_agent and "(" in user_agent and ")" in user_agent)
