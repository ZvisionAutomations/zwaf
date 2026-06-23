"""Deterministic delivery-address parser + ViaCEP resolver (story-040).

Resolve o atrito de checkout do caso Fernando: endereco em texto livre/string que
o LLM nao segmenta corretamente. O fluxo:

1. parse_free_text_address: extrai postal_code (regex CEP), number e complement de
   texto livre, cobrindo "930 casa 5", "n 930", "930/5", "930 - casa 5".
2. resolve_delivery_address: aceita str OU dict, parseia, chama ViaCEP e faz o
   merge FR-5 (CEP e fonte de verdade para street/district/city/state;
   number/complement vem sempre do cliente) com fallback resiliente FR-6
   (ViaCEP None -> usa os campos que o LLM/cliente forneceu).

NUNCA levanta excecao para o checkout (NFR-2). PII nao e logada (NFR-4).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from zwaf.integrations.viacep import resolve_cep
from zwaf.security.pii import only_digits

logger = logging.getLogger("zwaf.conversion.address_resolver")

# CEP: 5 digitos + opcional separador + 3 digitos (FR-2).
_CEP_RE = re.compile(r"\b(\d{5})-?\.?\s?(\d{3})\b")

# Numero do endereco com complemento opcional (FR-3). Cobre:
#   "930", "n 930", "nº 930", "no 930", "numero 930", "930 casa 5",
#   "930/5", "930 - casa 5", "52, casa 97" (separador virgula — story-063).
_NUMBER_RE = re.compile(
    r"(?:n[ºo°.]?\s*|numero\s+|num\s+)?"   # prefixo opcional "n", "nº", "numero"
    r"(?P<number>\d{1,6})"                   # o numero em si
    r"(?P<complement>"
    r"\s*[/-]\s*\w.*"                          # "930/5", "930 - casa 5"
    r"|[\s,]+(?:casa|apto|apartamento|bloco|fundos|sala|lote|cs|ap)\b.*"  # "930 casa 5", "52, casa 97"
    r")?",
    re.IGNORECASE,
)

# Palavras que tipicamente abrem um complemento (para limpar a captura).
_COMPLEMENT_CLEAN_RE = re.compile(r"^[\s/,-]+")

# CPF em texto livre (11 digitos, com ou sem mascara) — story-074 BUG-2:
# precisa sair do texto ANTES da extracao do numero da casa, senao o
# _NUMBER_RE captura os 6 primeiros digitos do CPF como "numero" (caso Fernando:
# CPF 21722244801 virava numero "217222" em vez de "930").
_CPF_RE = re.compile(r"\b\d{3}\.?\s?\d{3}\.?\s?\d{3}-?\s?\d{2}\b")


def _extract_postal_code(text: str) -> str:
    match = _CEP_RE.search(text or "")
    if not match:
        return ""
    return match.group(1) + match.group(2)


def _strip_cep_from_text(text: str) -> str:
    """Remove o CEP do texto para nao confundir a extracao de numero."""
    return _CEP_RE.sub(" ", text or "")


def _strip_cpf_from_text(text: str) -> str:
    """Remove o CPF (11 digitos) do texto para nao confundir a extracao de numero
    da casa (story-074 BUG-2)."""
    return _CPF_RE.sub(" ", text or "")


def _extract_number_and_complement(text: str) -> tuple[str, str]:
    """Extrai (number, complement) de texto livre, ignorando o CEP e o CPF."""
    cleaned = _strip_cpf_from_text(_strip_cep_from_text(text))
    match = _NUMBER_RE.search(cleaned)
    if not match:
        return "", ""
    number = match.group("number") or ""
    complement_raw = match.group("complement") or ""
    complement = _COMPLEMENT_CLEAN_RE.sub("", complement_raw).strip()
    return number, complement


def parse_free_text_address(text: str) -> dict[str, str]:
    """Extrai postal_code, number e complement de um endereco em texto livre.

    Nunca retorna {} silencioso para uma string nao-vazia: sempre devolve as
    chaves conhecidas (vazias quando nao encontradas).
    """
    text = (text or "").strip()
    postal_code = _extract_postal_code(text)
    number, complement = _extract_number_and_complement(text)
    parsed: dict[str, str] = {
        "postal_code": postal_code,
        "number": number,
        "complement": complement,
    }
    return parsed


def _coerce_to_dict(delivery_address: Any) -> dict[str, str]:
    """Normaliza str|dict para um dict[str,str] (FR-1: string nunca vira {}).

    - dict: stringifica/strip dos valores; se houver postal_code, normaliza
      digitos; tambem tenta enriquecer com parse do texto livre presente em
      campos como 'raw'/'address'/'full'/'text'/'logradouro'.
    - str: parseia o texto livre.
    """
    if isinstance(delivery_address, str):
        return parse_free_text_address(delivery_address)

    if isinstance(delivery_address, dict):
        normalized: dict[str, str] = {}
        for key, value in delivery_address.items():
            normalized[str(key)] = str(value or "").strip()
        # Se o dict trouxe um bloco de texto livre, aproveita para extrair
        # CEP/numero/complemento que possam estar la (defensivo).
        free_text = " ".join(
            normalized.get(k, "")
            for k in ("raw", "address", "full", "text", "endereco", "complemento", "street")
        ).strip()
        if free_text:
            parsed = parse_free_text_address(free_text)
            for k, v in parsed.items():
                if v and not normalized.get(k):
                    normalized[k] = v
        if normalized.get("postal_code"):
            normalized["postal_code"] = only_digits(normalized["postal_code"])
        if normalized.get("state"):
            normalized["state"] = normalized["state"].upper()
        return normalized

    # Qualquer outro tipo (None, etc.) -> dict vazio mas previsivel.
    return {}


def _merge_with_viacep(
    base: dict[str, str],
    viacep: Optional[dict[str, str]],
) -> dict[str, str]:
    """Aplica FR-5: CEP (ViaCEP) e fonte de verdade para street/district/city/state.

    number/complement vem sempre do cliente (base). Quando o ViaCEP nao resolve
    (None) ou nao traz um campo, mantem o que o LLM/cliente forneceu (FR-6).
    """
    merged = dict(base)
    if viacep:
        for key in ("street", "district", "city", "state"):
            value = viacep.get(key, "")
            if value:
                merged[key] = value.upper() if key == "state" else value
            else:
                merged.setdefault(key, base.get(key, ""))
    # number/complement nunca sao sobrescritos pelo ViaCEP.
    merged["number"] = base.get("number", "")
    if base.get("complement"):
        merged["complement"] = base["complement"]
    if merged.get("state"):
        merged["state"] = merged["state"].upper()
    if merged.get("postal_code"):
        merged["postal_code"] = only_digits(merged["postal_code"])
    return merged


async def resolve_delivery_address(
    delivery_address: Any,
    *,
    timeout: float = 3.0,
    viacep_resolver=resolve_cep,
) -> dict[str, str]:
    """Resolve um endereco (str|dict) para um dict estruturado completo.

    1. Coage para dict (parseando texto livre quando string) — FR-1.
    2. Se houver CEP, consulta o ViaCEP — FR-4.
    3. Faz o merge FR-5 e aplica fallback resiliente FR-6.

    NUNCA levanta excecao (NFR-2): em qualquer erro inesperado, devolve o melhor
    dict possivel a partir dos dados ja disponiveis.

    viacep_resolver: injetavel para testes (default: integrations.viacep.resolve_cep).
    """
    try:
        base = _coerce_to_dict(delivery_address)
    except Exception as exc:  # defensivo — nunca travar o checkout
        logger.warning("address_resolver: coerce failed: %s", type(exc).__name__)
        return {}

    postal_code = only_digits(base.get("postal_code", ""))
    if len(postal_code) != 8:
        # Sem CEP valido nao ha o que resolver via ViaCEP — devolve o que tem.
        return base

    try:
        viacep = await viacep_resolver(postal_code, timeout=timeout)
    except Exception as exc:  # defensivo — ViaCEP ja trata interno, mas garante
        logger.warning("address_resolver: viacep raised %s, using fallback", type(exc).__name__)
        viacep = None

    return _merge_with_viacep(base, viacep)
