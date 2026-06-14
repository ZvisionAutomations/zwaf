"""
Story 047 social proof harness.

Dry run only: validates that the catalog contract sends exactly four approved images
through the WhatsApp media tool boundary without calling Evolution API.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from unittest.mock import patch

from zwaf.tools.base import ToolResult
from zwaf.tools.social_proof import make_social_proof_sender


class DryRunWhatsAppTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

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
        return ToolResult.ok({"status": "dry_run", "asset_id": asset_id})


def _catalog() -> dict:
    assets = []
    for index in range(1, 5):
        assets.append(
            {
                "asset_id": f"livia_social_proof_{index:03d}",
                "status": "active",
                "media_url": f"https://example.invalid/livia-social-proof-{index}.jpg",
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
        )
    return {
        "tenant_id": "livia-raiz-vital",
        "sequence_size": 4,
        "assets": assets,
    }


async def run(phone: str) -> None:
    with patch("zwaf.tools.social_proof._load_catalog", return_value=(_catalog(), None)):
        whatsapp = DryRunWhatsAppTool()
        sender = make_social_proof_sender(
            "livia-raiz-vital",
            whatsapp,
            Path("in-memory-catalog.json"),
        )

        result = await sender(phone=phone, trigger="explicit_request", session_id="harness_story_047")

    if not result["success"]:
        print(f"FAIL story-047 social proof: {result['error']}")
        raise SystemExit(1)
    if len(whatsapp.calls) != 4:
        print(f"FAIL story-047 social proof: expected 4 calls, got {len(whatsapp.calls)}")
        raise SystemExit(1)
    print("PASS story-047 social proof dry-run: 4 media sends validated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Story 047 social proof dry-run harness")
    parser.add_argument("--phone", default="5511999990001")
    args = parser.parse_args()
    asyncio.run(run(args.phone))
