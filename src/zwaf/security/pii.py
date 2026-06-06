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


def is_valid_document(document: str) -> bool:
    digits = only_digits(document)
    if len(digits) == 11:
        return _is_valid_cpf(digits)
    if len(digits) == 14:
        return _is_valid_cnpj(digits)
    return False


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


def _is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    nums = [int(ch) for ch in digits]
    for factor_start, expected_index in ((10, 9), (11, 10)):
        total = sum(nums[i] * (factor_start - i) for i in range(expected_index))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if nums[expected_index] != check:
            return False
    return True


def _is_valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or digits == digits[0] * 14:
        return False
    nums = [int(ch) for ch in digits]
    weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights_2 = [6] + weights_1

    def _calc(nums_slice: list[int], weights: list[int]) -> int:
        total = sum(n * w for n, w in zip(nums_slice, weights))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    first = _calc(nums[:12], weights_1)
    if nums[12] != first:
        return False
    second = _calc(nums[:13], weights_2)
    return nums[13] == second
