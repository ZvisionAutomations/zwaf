from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zwaf.tools.base import ToolResult
from zwaf.tools.social_proof import make_social_proof_sender


class FakeWhatsAppTool:
    def __init__(self, fail_on_asset_id: str | None = None):
        self.fail_on_asset_id = fail_on_asset_id
        self.calls = []

    async def send_image(
        self,
        phone: str,
        media_url: str | None = None,
        media_path: str | None = None,
        caption: str = "",
        session_id: str | None = None,
        asset_id: str | None = None,
    ) -> ToolResult:
        self.calls.append(
            {
                "phone": phone,
                "media_url": media_url,
                "media_path": media_path,
                "caption": caption,
                "session_id": session_id,
                "asset_id": asset_id,
            }
        )
        if asset_id == self.fail_on_asset_id:
            return ToolResult.fail("evolution_error")
        return ToolResult.ok({"status": "sent", "asset_id": asset_id})


def _write_catalog(path: Path, assets: list[dict], sequence_size: int = 4) -> None:
    path.write_text(
        json.dumps(
            {
                "tenant_id": "livia-raiz-vital",
                "sequence_size": sequence_size,
                "assets": assets,
            }
        ),
        encoding="utf-8",
    )


def test_load_catalog_accepts_utf8_bom(tmp_path: Path):
    from zwaf.tools.social_proof import _load_catalog

    payload = {
        "tenant_id": "livia-raiz-vital",
        "sequence_size": 4,
        "assets": [],
    }

    class BomCatalogPath:
        def exists(self) -> bool:
            return True

        def read_text(self, encoding: str) -> str:
            assert encoding == "utf-8-sig"
            return "\ufeff" + json.dumps(payload)

    catalog, error = _load_catalog(BomCatalogPath())

    assert error is None
    assert catalog["tenant_id"] == "livia-raiz-vital"


def _asset(index: int, **overrides) -> dict:
    data = {
        "asset_id": f"livia_social_proof_{index:03d}",
        "status": "active",
        "media_url": f"https://cdn.example.test/prova-{index}.jpg",
        "media_path": "",
        "caption": "Foto real aprovada do New Woman.",
        "caption_approved": True,
        "approved_by": "Fernando",
        "approved_at": "2026-06-13",
        "consent_scope": "whatsapp_sales_social_proof",
        "consent_obtained_at": "2026-06-13",
        "pii_review": "approved",
        "claim_level": "visual_only",
    }
    data.update(overrides)
    return data


async def _run_with_catalog(assets: list[dict], fake_whatsapp: FakeWhatsAppTool, **kwargs) -> dict:
    catalog = {
        "tenant_id": "livia-raiz-vital",
        "sequence_size": kwargs.pop("sequence_size", 4),
        "assets": assets,
    }
    with patch("zwaf.tools.social_proof._load_catalog", return_value=(catalog, None)):
        sender = make_social_proof_sender(
            "livia-raiz-vital",
            fake_whatsapp,
            Path("in-memory-catalog.json"),
        )
        return await sender(phone="5511999990001", **kwargs)


@pytest.mark.asyncio
async def test_send_social_proof_sends_exactly_four_assets():
    fake_whatsapp = FakeWhatsAppTool()

    result = await _run_with_catalog(
        [_asset(i) for i in range(1, 5)],
        fake_whatsapp,
        session_id="sess-047",
        trigger="accepted_offer",
        consent_confirmed=True,
    )

    assert result == {
        "success": True,
        "sent_count": 4,
        "asset_ids": [
            "livia_social_proof_001",
            "livia_social_proof_002",
            "livia_social_proof_003",
            "livia_social_proof_004",
        ],
        "failed_asset_id": None,
        "error": None,
    }
    assert len(fake_whatsapp.calls) == 4
    assert all(call["caption"] == "Foto real aprovada do New Woman." for call in fake_whatsapp.calls)


@pytest.mark.asyncio
async def test_send_social_proof_requires_consent_for_accepted_offer():
    fake_whatsapp = FakeWhatsAppTool()

    result = await _run_with_catalog(
        [_asset(i) for i in range(1, 5)],
        fake_whatsapp,
        trigger="accepted_offer",
    )

    assert result["success"] is False
    assert result["error"] == "social_proof_consent_required"
    assert fake_whatsapp.calls == []


@pytest.mark.asyncio
async def test_send_social_proof_allows_explicit_request_without_extra_consent():
    fake_whatsapp = FakeWhatsAppTool()

    result = await _run_with_catalog(
        [_asset(i) for i in range(1, 5)],
        fake_whatsapp,
        trigger="explicit_request",
    )

    assert result["success"] is True
    assert len(fake_whatsapp.calls) == 4


@pytest.mark.asyncio
async def test_send_social_proof_rejects_partial_catalog():
    fake_whatsapp = FakeWhatsAppTool()

    result = await _run_with_catalog(
        [_asset(i) for i in range(1, 4)],
        fake_whatsapp,
        trigger="explicit_request",
    )

    assert result["success"] is False
    assert result["error"] == "social_proof_catalog_not_ready"
    assert fake_whatsapp.calls == []


@pytest.mark.asyncio
async def test_send_social_proof_rejects_forbidden_claim_caption():
    assets = [_asset(i) for i in range(1, 5)]
    assets[0]["caption"] = "Antes/depois com resultado garantido."
    fake_whatsapp = FakeWhatsAppTool()

    result = await _run_with_catalog(assets, fake_whatsapp, trigger="explicit_request")

    assert result["success"] is False
    assert result["error"] == "social_proof_catalog_not_ready"
    assert fake_whatsapp.calls == []


@pytest.mark.asyncio
async def test_send_social_proof_stops_on_first_failed_media():
    fake_whatsapp = FakeWhatsAppTool(fail_on_asset_id="livia_social_proof_003")

    result = await _run_with_catalog(
        [_asset(i) for i in range(1, 5)],
        fake_whatsapp,
        trigger="explicit_request",
    )

    assert result["success"] is False
    assert result["sent_count"] == 2
    assert result["asset_ids"] == ["livia_social_proof_001", "livia_social_proof_002"]
    assert result["failed_asset_id"] == "livia_social_proof_003"
    assert len(fake_whatsapp.calls) == 3
