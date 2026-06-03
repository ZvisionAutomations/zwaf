"""Langfuse observability: masking, identifiers, enablement and fallback."""
from __future__ import annotations

from zwaf.observability import langfuse as obs


def test_mask_pii_removes_phone_email_document_and_keys():
    raw = (
        "fala com 5511980142484, email joao@raizvital.com.br, "
        "cpf 123.456.789-01, cnpj 12.345.678/0001-99, "
        "token sk-abcdef1234567890 Bearer abcdef123456 "
        "jwt eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36"
    )
    masked = obs.mask_pii(raw)

    assert "5511980142484" not in masked
    assert "joao@raizvital.com.br" not in masked
    assert "123.456.789-01" not in masked
    assert "12.345.678/0001-99" not in masked
    assert "sk-abcdef1234567890" not in masked
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masked


def test_phone_tail_keeps_last_four():
    assert obs.phone_tail("5511980142484") == "2484"
    assert obs.phone_tail("123") == ""


def test_stable_id_is_deterministic_and_non_reversible(monkeypatch):
    monkeypatch.setenv("ZWAF_PII_HASH_SALT", "fixed-salt")
    a = obs.stable_id("livia-raiz-vital:5511980142484", prefix="sess_")
    b = obs.stable_id("livia-raiz-vital:5511980142484", prefix="sess_")
    assert a == b
    assert a.startswith("sess_")
    assert "5511980142484" not in a


def test_is_enabled_false_for_placeholders(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-...")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-aqui")
    assert obs.is_enabled() is False


def test_is_enabled_true_for_real_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-realpublic123")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-realsecret456")
    assert obs.is_enabled() is True


def test_build_trace_metadata_allowlist_and_masking():
    meta = obs.build_trace_metadata(
        {
            "tenant_id": "livia-raiz-vital",
            "agent_used": "vendedor",
            "latency_ms": 1234,
            "phone_tail": "2484",
            "secret_field": "should-be-dropped",
            "error": "falha com token sk-abcdef1234567890",
        }
    )
    assert meta["tenant_id"] == "livia-raiz-vital"
    assert meta["latency_ms"] == 1234
    assert "secret_field" not in meta
    assert "sk-abcdef1234567890" not in meta["error"]


def test_record_conversation_is_silent_when_disabled(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    # Must not raise even with no SDK / no config.
    obs.record_conversation(
        name="t",
        session_seed="x:y",
        user_seed="y",
        metadata={"tenant_id": "t"},
        tags=["tenant:t"],
    )
