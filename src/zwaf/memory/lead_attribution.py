"""First-touch Click-to-WhatsApp attribution persistence."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("zwaf.memory.lead_attribution")


@dataclass(frozen=True)
class LeadAttribution:
    tenant_id: str
    session_id: str
    lead_phone: str
    ctwa_clid: str = ""
    source_id: str = ""
    source_type: str = ""
    source_url: str = ""
    headline: str = ""

    @property
    def has_signal(self) -> bool:
        return any((self.ctwa_clid, self.source_id, self.source_url, self.headline))


def extract_attribution(payload: dict[str, Any], *, tenant_id: str, session_id: str, phone: str) -> LeadAttribution:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    message = data.get("message", {}) if isinstance(data, dict) else {}
    context = _find_context_info(message)
    external = context.get("externalAdReply", {}) or context.get("external_ad_reply", {})
    referral = context.get("referral", {}) or {}

    return LeadAttribution(
        tenant_id=tenant_id,
        session_id=session_id,
        lead_phone=phone,
        ctwa_clid=str(
            context.get("ctwa_clid")
            or context.get("ctwaClid")
            or referral.get("ctwa_clid")
            or referral.get("ctwaClid")
            or ""
        ),
        source_id=str(
            external.get("sourceId")
            or external.get("source_id")
            or referral.get("source_id")
            or referral.get("sourceId")
            or ""
        ),
        source_type=str(
            external.get("sourceType")
            or external.get("source_type")
            or referral.get("source_type")
            or referral.get("sourceType")
            or ""
        ),
        source_url=str(
            external.get("sourceUrl")
            or external.get("source_url")
            or referral.get("source_url")
            or referral.get("sourceUrl")
            or ""
        ),
        headline=str(
            external.get("title")
            or external.get("body")
            or referral.get("headline")
            or referral.get("title")
            or ""
        ),
    )


async def record_lead_attribution(attribution: LeadAttribution) -> str:
    if not attribution.has_signal:
        return "ignored_no_signal"
    db_url = _db_url()
    if not db_url:
        return "accepted_no_db"
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        try:
            status = await conn.execute(
                """
                INSERT INTO lead_attribution (
                    tenant_id, session_id, lead_phone, ctwa_clid,
                    source_id, source_type, source_url, headline
                )
                VALUES ($1, $2, $3, NULLIF($4, ''), NULLIF($5, ''),
                        NULLIF($6, ''), NULLIF($7, ''), NULLIF($8, ''))
                ON CONFLICT (tenant_id, session_id) DO NOTHING
                """,
                attribution.tenant_id,
                attribution.session_id,
                attribution.lead_phone,
                attribution.ctwa_clid,
                attribution.source_id,
                attribution.source_type,
                attribution.source_url,
                attribution.headline,
            )
        finally:
            await conn.close()
        return "inserted" if _inserted(status) else "duplicate_first_touch"
    except Exception as exc:
        logger.warning("lead attribution persistence failed: %s", exc)
        return "accepted_db_error"


def _find_context_info(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    candidates = [
        message.get("contextInfo"),
        message.get("extendedTextMessage", {}).get("contextInfo"),
        message.get("imageMessage", {}).get("contextInfo"),
        message.get("videoMessage", {}).get("contextInfo"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _inserted(command_status: str) -> bool:
    return str(command_status).strip().endswith(" 1")


def _db_url() -> str:
    return (os.getenv("DATABASE_URL") or "").replace("+asyncpg", "")
