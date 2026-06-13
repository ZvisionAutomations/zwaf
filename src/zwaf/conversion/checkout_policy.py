"""Deterministic checkout and contact policy for pre-WhatsApp hardening."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from zwaf.security.pii import document_type, is_valid_document, only_digits

REQUIRED_ADDRESS_FIELDS = ("postal_code", "street", "number", "district", "city", "state")

OPT_OUT_PHRASES = (
    "nao tenho interesse",
    "não tenho interesse",
    "pare",
    "parar",
    "sair",
    "remover",
    "cancele",
    "cancelar",
    "descadastrar",
    "nao me chame",
    "não me chame",
    "nao mandar mensagem",
    "não mandar mensagem",
)

CRITICAL_COMPLAINT_PHRASES = (
    "reacao adversa",
    "reação adversa",
    "efeito colateral",
    "passei mal",
    "alergia",
    "reembolso",
    "devolver",
    "defeito",
    "danificado",
    "veio quebrado",
    "produto errado",
    "reclamacao",
    "reclamação",
    "procon",
)


@dataclass(frozen=True)
class CheckoutValidation:
    ok: bool
    code: str
    missing_fields: list[str] = field(default_factory=list)
    message: str = ""


def validate_checkout_ready(
    *,
    tenant_id: str,
    product_id: str,
    customer_name: str,
    customer_document: str,
    delivery_address: Any,
    billing_type: str = "PIX",
) -> CheckoutValidation:
    normalized_product = (product_id or "").replace("_", "-").lower()
    if tenant_id == "livia-raiz-vital" and normalized_product.startswith("alpha-pulse"):
        return CheckoutValidation(
            ok=False,
            code="blocked_product",
            message="Alpha Pulse deve ser atendido pelo consultor masculino/Caio.",
        )
    if tenant_id == "caio-alpha-pulse" and normalized_product.startswith("new-woman"):
        return CheckoutValidation(
            ok=False,
            code="blocked_product",
            message="New Woman deve ser atendido pela Livia, consultora da Raiz Vital.",
        )

    if (billing_type or "PIX").upper() == "CREDIT_CARD":
        return CheckoutValidation(ok=True, code="checkout_ready")

    missing: list[str] = []
    if not _has_full_name(customer_name):
        missing.append("customer_name")
    document = (customer_document or "").strip()
    if not document:
        # documento nao informado
        missing.append("customer_document")
    elif document_type(document) == "unknown" or not is_valid_document(document):
        # Documento informado, porem estruturalmente invalido (DV/tamanho).
        missing.append("customer_document_invalid")

    address = normalize_delivery_address(delivery_address)
    for field_name in REQUIRED_ADDRESS_FIELDS:
        if not address.get(field_name, "").strip():
            missing.append(f"delivery_address.{field_name}")

    if missing:
        return CheckoutValidation(
            ok=False,
            code="missing_required_data",
            missing_fields=missing,
            message="Antes do link, preciso dos dados minimos do pedido.",
        )

    return CheckoutValidation(ok=True, code="checkout_ready")


def normalize_delivery_address(delivery_address: Any) -> dict[str, str]:
    """Normaliza um endereco para dict[str,str] (story-040 FR-1).

    Aceita tanto dict quanto string em texto livre. String NAO retorna mais {}
    silencioso: o parser deterministico extrai CEP/numero/complemento. Esta funcao
    e SINCRONA e NAO faz rede (sem ViaCEP) — o enriquecimento via ViaCEP acontece
    no caminho async (resolve_delivery_address / payment_gate).
    """
    if isinstance(delivery_address, str):
        # Import tardio para evitar ciclo (address_resolver importa pii daqui via
        # security, mas nao este modulo; ainda assim mantemos lazy por seguranca).
        from zwaf.conversion.address_resolver import parse_free_text_address

        parsed = parse_free_text_address(delivery_address)
        if parsed.get("postal_code"):
            parsed["postal_code"] = only_digits(parsed["postal_code"])
        return parsed
    if not isinstance(delivery_address, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in delivery_address.items():
        normalized[str(key)] = str(value or "").strip()
    if "postal_code" in normalized:
        normalized["postal_code"] = only_digits(normalized["postal_code"])
    if "state" in normalized:
        normalized["state"] = normalized["state"].upper()
    return normalized


def is_opt_out_message(message: str) -> bool:
    normalized = _normalize_text(message)
    if not normalized:
        return False
    return any(_phrase_matches(normalized, _normalize_text(phrase)) for phrase in OPT_OUT_PHRASES)


def is_critical_complaint(message: str) -> bool:
    normalized = _normalize_text(message)
    if not normalized:
        return False
    return any(phrase in normalized for phrase in map(_normalize_text, CRITICAL_COMPLAINT_PHRASES))


def _has_full_name(name: str) -> bool:
    parts = [part for part in (name or "").strip().split() if len(part) >= 2]
    return len(parts) >= 2


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def _phrase_matches(message: str, phrase: str) -> bool:
    if len(phrase.split()) == 1:
        return re.search(rf"(^|\W){re.escape(phrase)}(\W|$)", message) is not None
    return phrase in message
