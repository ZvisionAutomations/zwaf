"""Langfuse observability wrapper for ZWAF.

Design goals (Story 026):
- Zero impact on the customer flow: every call is best-effort and fails silently.
- Never send PII (full phone, email, CPF/CNPJ), tokens or raw payloads.
- Stable, hashed identifiers for session/user so traces can be grouped without
  exposing the phone number.
- Works whether or not the `langfuse` SDK is installed or configured.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("zwaf.observability.langfuse")

_PLACEHOLDER_MARKERS = ("aqui", "placeholder", "sk-lf-...", "pk-lf-...", "...", "changeme")

# --- PII / secret masking ---------------------------------------------------

_MASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI / Langfuse / generic provider keys
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "[redacted-key]"),
    (re.compile(r"sk-lf-[A-Za-z0-9_\-]{8,}"), "[redacted-key]"),
    (re.compile(r"pk-lf-[A-Za-z0-9_\-]{8,}"), "[redacted-key]"),
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{8,}"), "[redacted-key]"),
    # Bearer / Authorization / access_token
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "[redacted-token]"),
    (re.compile(r"(?i)(authorization|access_token|api_key)\s*[:=]\s*\S+"), r"\1=[redacted]"),
    # JWT
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "[redacted-jwt]"),
    # Email
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[redacted-email]"),
    # CPF / CNPJ (with or without punctuation): 11 or 14 digits
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[redacted-doc]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[redacted-doc]"),
    # Brazilian phone with country code (keep last 4 only)
    (re.compile(r"\b55\d{2}9?\d{4}(\d{4})\b"), r"[redacted-phone-\1]"),
    # Long digit runs (>=8) that could be phone/document fragments
    (re.compile(r"\b\d{8,}\b"), "[redacted-number]"),
)


def mask_pii(text: Any) -> str:
    """Mask phones, emails, documents, tokens and keys from free text."""
    if text is None:
        return ""
    masked = str(text)
    for pattern, replacement in _MASK_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def phone_tail(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    return digits[-4:] if len(digits) >= 4 else ""


def stable_id(value: str, prefix: str = "") -> str:
    """Deterministic, non-reversible id for grouping traces (HMAC-SHA256, 16 hex)."""
    if not value:
        return ""
    secret = (os.getenv("ZWAF_PII_HASH_SALT", "zwaf-local-pii-salt")).encode("utf-8")
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


# --- enablement -------------------------------------------------------------

def _is_real(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    lowered = value.lower()
    return not any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def is_enabled() -> bool:
    """True only when both Langfuse keys look real (not placeholders)."""
    return _is_real(os.getenv("LANGFUSE_PUBLIC_KEY", "")) and _is_real(
        os.getenv("LANGFUSE_SECRET_KEY", "")
    )


def langfuse_base_url() -> str:
    # LANGFUSE_BASE_URL is the current SDK var; LANGFUSE_HOST kept for compat.
    return (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").strip()


# --- allowlisted metadata ---------------------------------------------------

_ALLOWED_METADATA_KEYS = frozenset(
    {
        "tenant_id",
        "agent_used",
        "feature",
        "environment",
        "release",
        "phone_tail",
        "model",
        "latency_ms",
        "status",
        "error",
    }
)


def build_trace_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only allowlisted keys and mask their values."""
    metadata: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metadata[key] = value
        else:
            metadata[key] = mask_pii(value)
    return metadata


# --- client (best effort) ---------------------------------------------------

_client: Optional[Any] = None
_client_resolved = False


def _get_client() -> Optional[Any]:
    global _client, _client_resolved
    if _client_resolved:
        return _client
    _client_resolved = True
    if not is_enabled():
        _client = None
        return None
    try:
        from langfuse import Langfuse  # type: ignore

        kwargs: dict[str, Any] = {}
        base_url = langfuse_base_url()
        if base_url:
            kwargs["host"] = base_url
        _client = Langfuse(**kwargs)
    except Exception as exc:  # SDK missing or misconfigured
        logger.warning("Langfuse disabled: %s", mask_pii(str(exc)))
        _client = None
    return _client


def record_conversation(
    *,
    name: str,
    session_seed: str,
    user_seed: str,
    metadata: dict[str, Any],
    tags: Optional[list[str]] = None,
) -> None:
    """Best-effort trace of one conversation turn. Never raises.

    session_seed/user_seed are raw values (e.g. phone) that are hashed here;
    raw values are NEVER forwarded to Langfuse.
    """
    client = _get_client()
    if client is None:
        return
    try:
        safe_meta = build_trace_metadata(metadata)
        safe_tags = [mask_pii(t) for t in (tags or [])]
        trace_fn = getattr(client, "trace", None)
        if callable(trace_fn):
            trace_fn(
                name=name,
                session_id=stable_id(session_seed, prefix="sess_"),
                user_id=stable_id(user_seed, prefix="lead_"),
                tags=safe_tags,
                metadata=safe_meta,
            )
    except Exception as exc:
        logger.warning("Langfuse trace failed: %s", mask_pii(str(exc)))


def flush() -> None:
    client = _get_client()
    if client is None:
        return
    try:
        flush_fn = getattr(client, "flush", None)
        if callable(flush_fn):
            flush_fn()
    except Exception:
        pass
