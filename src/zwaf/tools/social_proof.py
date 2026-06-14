"""Controlled social proof media sender for Livia."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.tools.social_proof")

_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"
_ALLOWED_TRIGGERS = {"explicit_request", "accepted_offer"}
_FORBIDDEN_CAPTION_RE = re.compile(
    r"\b(cura|milagre|garantia|resultado garantido|equilibrio hormonal garantido|"
    r"antes/depois|antes e depois|melhorou\s+\d+%|\d+%|milhares|centenas|"
    r"hoje estao muito melhores)\b",
    re.IGNORECASE,
)


def make_social_proof_sender(
    tenant_id: str,
    whatsapp_tool: WhatsAppTool,
    catalog_path: str | Path | None = None,
) -> Callable[..., Any]:
    """Factory used by the vendedor agent so tenant/tool are not LLM-controlled."""
    path = Path(catalog_path) if catalog_path else _catalog_path_for(tenant_id)

    async def send_social_proof(
        phone: str,
        session_id: str | None = None,
        asset_ids: list[str] | None = None,
        consent_confirmed: bool = False,
        trigger: str = "accepted_offer",
    ) -> dict[str, Any]:
        if trigger not in _ALLOWED_TRIGGERS:
            return _failure("social_proof_invalid_trigger")
        if trigger == "accepted_offer" and not consent_confirmed:
            return _failure("social_proof_consent_required")

        catalog, error = _load_catalog(path)
        if error:
            return _failure(error)
        if catalog.get("tenant_id") != tenant_id:
            return _failure("social_proof_catalog_tenant_mismatch")

        assets = [_asset_with_path(asset, path) for asset in catalog.get("assets", [])]
        valid_assets = [asset for asset in assets if _is_valid_active_asset(asset)]
        selected, error = _select_assets(
            valid_assets,
            asset_ids,
            int(catalog.get("sequence_size", 4)),
        )
        if error:
            return _failure(error)

        sent_ids: list[str] = []
        for asset in selected:
            result = await whatsapp_tool.send_image(
                phone=phone,
                media_url=asset.get("media_url") or None,
                media_path=asset.get("media_path") or None,
                caption=asset["caption"],
                session_id=session_id,
                asset_id=asset["asset_id"],
            )
            if not result.success:
                logger.warning(
                    "social_proof_send_failed",
                    extra={
                        "phone_tail": str(phone)[-4:],
                        "session_id": session_id,
                        "asset_id": asset["asset_id"],
                        "error": result.error,
                    },
                )
                return {
                    "success": False,
                    "sent_count": len(sent_ids),
                    "asset_ids": sent_ids,
                    "failed_asset_id": asset["asset_id"],
                    "error": "social_proof_send_failed",
                }
            sent_ids.append(asset["asset_id"])

        return {
            "success": True,
            "sent_count": len(sent_ids),
            "asset_ids": sent_ids,
            "failed_asset_id": None,
            "error": None,
        }

    send_social_proof.__name__ = "send_social_proof"
    return send_social_proof


def _catalog_path_for(tenant_id: str) -> Path:
    return _TENANTS_ROOT / tenant_id / "social-proof" / "catalog.json"


def _load_catalog(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "social_proof_catalog_not_found"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig").lstrip("\ufeff")), None
    except json.JSONDecodeError:
        return {}, "social_proof_catalog_invalid"


def _asset_with_path(asset: dict[str, Any], catalog_path: Path) -> dict[str, Any]:
    result = dict(asset)
    media_path = str(result.get("media_path") or "").strip()
    if media_path and not Path(media_path).is_absolute():
        result["media_path"] = str((catalog_path.parent / media_path).resolve())
    return result


def _is_valid_active_asset(asset: dict[str, Any]) -> bool:
    if asset.get("status") != "active":
        return False
    caption = str(asset.get("caption") or "").strip()
    media_url = str(asset.get("media_url") or "").strip()
    media_path = str(asset.get("media_path") or "").strip()
    return all(
        (
            bool(asset.get("asset_id")),
            bool(caption),
            not _FORBIDDEN_CAPTION_RE.search(caption),
            asset.get("caption_approved") is True,
            asset.get("approved_by") == "Fernando",
            bool(asset.get("approved_at")),
            bool(asset.get("consent_scope")),
            bool(asset.get("consent_obtained_at")),
            asset.get("pii_review") == "approved",
            asset.get("claim_level") == "visual_only",
            (bool(media_url) ^ bool(media_path)),
        )
    )


def _select_assets(
    valid_assets: list[dict[str, Any]],
    asset_ids: list[str] | None,
    sequence_size: int,
) -> tuple[list[dict[str, Any]], str | None]:
    if sequence_size != 4:
        return [], "social_proof_invalid_sequence_size"
    by_id = {asset["asset_id"]: asset for asset in valid_assets}
    if asset_ids:
        if len(asset_ids) != sequence_size:
            return [], "social_proof_invalid_asset_count"
        selected = []
        for asset_id in asset_ids:
            asset = by_id.get(asset_id)
            if asset is None:
                return [], "social_proof_asset_not_available"
            selected.append(asset)
        return selected, None
    if len(valid_assets) != sequence_size:
        return [], "social_proof_catalog_not_ready"
    return valid_assets, None


def _failure(error: str) -> dict[str, Any]:
    return {
        "success": False,
        "sent_count": 0,
        "asset_ids": [],
        "failed_asset_id": None,
        "error": error,
    }
