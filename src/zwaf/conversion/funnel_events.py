"""PII-safe funnel event primitives for Livia conversion analytics."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from zwaf.security.pii import hash_pii


class FunnelEventName(str, Enum):
    CONVERSATION_STARTED = "conversation_started"
    DIAGNOSIS_COMPLETED = "diagnosis_completed"
    PAIN_DIMENSIONED = "pain_dimensioned"
    OFFER_PRESENTED = "offer_presented"
    QUANTITY_RECOMMENDED = "quantity_recommended"
    OBJECTION_DETECTED = "objection_detected"
    OBJECTION_RESOLVED = "objection_resolved"
    CHECKOUT_REQUESTED = "checkout_requested"
    CHECKOUT_LINK_GENERATED = "checkout_link_generated"
    PAYMENT_CONFIRMED = "payment_confirmed"
    FOLLOWUP_SCHEDULED = "followup_scheduled"
    FOLLOWUP_SENT = "followup_sent"
    FOLLOWUP_REPLIED = "followup_replied"
    HANDOFF_TO_HUMAN = "handoff_to_human"
    OPT_OUT = "opt_out"


ALLOWED_EVENT_FIELDS = {
    "tenant_id",
    "session_hash",
    "event",
    "stage",
    "lead_temperature",
    "quantity",
    "amount_cents",
    "objection",
    "followup_sequence",
    "payment_status",
    "metadata",
}

SENSITIVE_FIELD_HINTS = (
    "phone",
    "telefone",
    "cpf",
    "cnpj",
    "email",
    "address",
    "endereco",
    "name",
    "nome",
    "token",
    "print",
)


@dataclass(frozen=True)
class FunnelEvent:
    tenant_id: str
    session_hash: str
    event: FunnelEventName
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event"] = self.event.value
        return data


def build_funnel_event(
    *,
    tenant_id: str,
    event: FunnelEventName | str,
    session_id: str,
    metadata: dict[str, Any] | None = None,
) -> FunnelEvent:
    """Build a funnel event with hash identifiers and sanitized metadata."""
    event_name = FunnelEventName(event)
    return FunnelEvent(
        tenant_id=tenant_id,
        session_hash=hash_pii(session_id, tenant_id=tenant_id),
        event=event_name,
        metadata=sanitize_event_metadata(metadata or {}),
    )


def sanitize_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Allow only safe analytical fields and redact PII-like values."""
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = str(key).lower()
        if normalized_key not in ALLOWED_EVENT_FIELDS:
            continue
        if any(hint in normalized_key for hint in SENSITIVE_FIELD_HINTS):
            continue
        safe[normalized_key] = _sanitize_value(value)
    return safe


def contains_sensitive_value(value: Any) -> bool:
    text = str(value or "")
    return any(
        (
            re.search(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", text),
            re.search(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", text),
            re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text),
            re.search(r"\b(?:\+?55)?\s?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b", text),
            "token" in text.lower(),
        )
    )


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_event_metadata(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value if not contains_sensitive_value(item)]
    if contains_sensitive_value(value):
        return "[redacted]"
    return value
