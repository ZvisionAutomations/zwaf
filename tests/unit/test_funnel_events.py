"""Unit tests for funnel_events primitives and emit_funnel_event integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zwaf.conversion.funnel_events import (
    ALLOWED_EVENT_FIELDS,
    FunnelEvent,
    FunnelEventName,
    build_funnel_event,
    contains_sensitive_value,
    sanitize_event_metadata,
)
from zwaf.observability import langfuse as obs


# ---------------------------------------------------------------------------
# build_funnel_event
# ---------------------------------------------------------------------------


def test_build_funnel_event_hashes_session_id(monkeypatch):
    monkeypatch.setenv("ZWAF_PII_HASH_SALT", "test-salt")
    raw_phone = "5511980001234"
    event = build_funnel_event(
        event=FunnelEventName.CONVERSATION_STARTED,
        session_id=raw_phone,
        tenant_id="livia-raiz-vital",
    )
    assert raw_phone not in event.session_hash
    assert len(event.session_hash) > 0


def test_build_funnel_event_returns_correct_fields():
    event = build_funnel_event(
        event=FunnelEventName.OPT_OUT,
        session_id="phone-123",
        tenant_id="tenant-abc",
        metadata={"stage": "checkout"},
    )
    assert event.tenant_id == "tenant-abc"
    assert event.event == FunnelEventName.OPT_OUT
    assert isinstance(event.metadata, dict)


def test_build_funnel_event_accepts_string_event():
    event = build_funnel_event(
        event="payment_confirmed",
        session_id="x",
        tenant_id="t",
    )
    assert event.event == FunnelEventName.PAYMENT_CONFIRMED


def test_build_funnel_event_rejects_unknown_event():
    with pytest.raises(ValueError):
        build_funnel_event(event="ghost_event", session_id="x", tenant_id="t")


# ---------------------------------------------------------------------------
# sanitize_event_metadata
# ---------------------------------------------------------------------------


def test_sanitize_event_metadata_drops_unknown_keys():
    raw = {"tenant_id": "t", "secret_key": "value", "stage": "checkout"}
    safe = sanitize_event_metadata(raw)
    assert "secret_key" not in safe
    assert safe["tenant_id"] == "t"
    assert safe["stage"] == "checkout"


def test_sanitize_event_metadata_drops_sensitive_hints():
    raw = {"phone": "5511999999999", "amount_cents": 14900}
    safe = sanitize_event_metadata(raw)
    assert "phone" not in safe
    assert safe.get("amount_cents") == 14900


def test_sanitize_event_metadata_redacts_pii_values():
    raw = {"stage": "123.456.789-01 presente aqui"}
    safe = sanitize_event_metadata(raw)
    assert "123.456.789-01" not in str(safe.get("stage", ""))


def test_sanitize_event_metadata_allows_all_allowed_fields():
    raw = {k: "val" for k in ALLOWED_EVENT_FIELDS}
    safe = sanitize_event_metadata(raw)
    # Non-sensitive hint keys should pass through.
    assert "tenant_id" in safe
    assert "stage" in safe
    assert "amount_cents" in safe


# ---------------------------------------------------------------------------
# contains_sensitive_value
# ---------------------------------------------------------------------------


def test_contains_sensitive_value_detects_cpf():
    assert contains_sensitive_value("cpf 123.456.789-01")


def test_contains_sensitive_value_detects_cnpj():
    assert contains_sensitive_value("12.345.678/0001-99")


def test_contains_sensitive_value_detects_email():
    assert contains_sensitive_value("joao@raizvital.com.br")


def test_contains_sensitive_value_detects_phone():
    assert contains_sensitive_value("(11) 98000-1234")


def test_contains_sensitive_value_safe_text_returns_false():
    assert not contains_sensitive_value("checkout iniciado")
    assert not contains_sensitive_value("2 potes")


# ---------------------------------------------------------------------------
# emit_funnel_event
# ---------------------------------------------------------------------------


def test_emit_funnel_event_calls_trace_when_enabled(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-realpublic123")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-realsecret456")

    mock_client = MagicMock()
    with patch("zwaf.observability.langfuse._get_client", return_value=mock_client):
        event = build_funnel_event(
            event=FunnelEventName.CHECKOUT_REQUESTED,
            session_id="5511980001234",
            tenant_id="livia-raiz-vital",
        )
        obs.emit_funnel_event(event)

    mock_client.trace.assert_called_once()
    call_kwargs = mock_client.trace.call_args.kwargs
    assert "funnel:checkout_requested" in call_kwargs["name"]
    assert call_kwargs["session_id"].startswith("sess_")


def test_emit_funnel_event_silent_when_client_none():
    with patch("zwaf.observability.langfuse._get_client", return_value=None):
        event = build_funnel_event(
            event=FunnelEventName.OPT_OUT,
            session_id="x",
            tenant_id="t",
        )
        # Must not raise.
        obs.emit_funnel_event(event)


def test_emit_funnel_event_silent_on_trace_exception():
    mock_client = MagicMock()
    mock_client.trace.side_effect = RuntimeError("network error")
    with patch("zwaf.observability.langfuse._get_client", return_value=mock_client):
        event = build_funnel_event(
            event=FunnelEventName.PAYMENT_CONFIRMED,
            session_id="x",
            tenant_id="t",
            metadata={"amount_cents": 14900},
        )
        # Must not raise.
        obs.emit_funnel_event(event)


def test_emit_funnel_event_metadata_has_no_raw_pii():
    mock_client = MagicMock()
    with patch("zwaf.observability.langfuse._get_client", return_value=mock_client):
        event = build_funnel_event(
            event=FunnelEventName.DIAGNOSIS_COMPLETED,
            session_id="5511980001234",
            tenant_id="t",
            metadata={"lead_temperature": "HIGH"},
        )
        obs.emit_funnel_event(event)

    call_kwargs = mock_client.trace.call_args.kwargs
    meta_str = str(call_kwargs.get("metadata", ""))
    assert "5511980001234" not in meta_str


def test_funnel_event_to_dict_serializes_enum():
    event = build_funnel_event(
        event=FunnelEventName.HANDOFF_TO_HUMAN,
        session_id="x",
        tenant_id="t",
    )
    d = event.to_dict()
    assert d["event"] == "handoff_to_human"
    assert isinstance(d["tenant_id"], str)
