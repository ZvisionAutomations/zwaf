"""PII helpers for backend-only encrypted persistence."""
from __future__ import annotations

import hashlib
import logging
import os
import re

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("zwaf.security.pii")

_DIGITS_RE = re.compile(r"\D+")


def only_digits(value: str) -> str:
    return _DIGITS_RE.sub("", value or "")


def document_last4(document: str) -> str:
    digits = only_digits(document)
    return digits[-4:] if len(digits) >= 4 else ""


def document_type(document: str) -> str:
    digits = only_digits(document)
    if len(digits) == 11:
        return "cpf"
    if len(digits) == 14:
        return "cnpj"
    return "unknown"


def hash_pii(value: str, tenant_id: str = "") -> str:
    normalized = only_digits(value) or (value or "").strip().lower()
    if not normalized:
        return ""
    salt = os.getenv("ZWAF_PII_HASH_SALT", "zwaf-local-pii-salt")
    payload = f"{salt}:{tenant_id}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def can_encrypt_pii() -> bool:
    return bool(_fernet_key())


def encrypt_pii(value: str) -> str:
    if not value:
        return ""
    key = _fernet_key()
    if not key:
        logger.warning("PII encryption key not configured")
        return ""
    return Fernet(key).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_pii(token: str) -> str:
    if not token:
        return ""
    key = _fernet_key()
    if not key:
        return ""
    try:
        return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("Invalid encrypted PII token")
        return ""


def _fernet_key() -> bytes:
    raw = os.getenv("ZWAF_PII_FERNET_KEY") or os.getenv("DOCUMENT_ENCRYPTION_KEY", "")
    return raw.encode("utf-8") if raw else b""
