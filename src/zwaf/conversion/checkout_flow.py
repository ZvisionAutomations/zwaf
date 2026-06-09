"""Coleta deterministica de checkout (story-041).

Tira a coleta de dados do LLM. Quando o lead confirma a compra, o ZWAFTeam entra
em "modo checkout" e estas funcoes conduzem a coleta de forma deterministica:

- leem o formato ROTULADO (``Nome:``/``CPF:``/``CEP:``/``Numero:``) com alta
  precisao e, como fallback, texto livre (reuso do parser da story-040);
- ACUMULAM os campos: so um valor VALIDO entra no estado, e um campo ja valido
  NUNCA e pedido de novo (requisito central da story — evita o loop "manda o CEP
  de novo");
- pedem apenas o que faltou/ficou invalido, nominalmente.

As funcoes de parse/merge/validacao sao puras e sincronas (testaveis offline).
``advance_checkout`` e a unica async (resolve o CEP via ViaCEP, FR-4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from zwaf.conversion.address_resolver import parse_free_text_address, resolve_delivery_address
from zwaf.security.pii import is_valid_document, only_digits

# Campos minimos para gerar o Pix. street/district/city/state vem do ViaCEP a
# partir do CEP; so sao cobrados se o ViaCEP nao resolver (fallback).
REQUIRED_FIELDS = ("name", "document", "postal_code", "number")
ADDRESS_FALLBACK_FIELDS = ("street", "district", "city", "state")

# Rotulos aceitos por campo (case-insensitive). O cliente e orientado a usar o
# modelo rotulado; isso elimina a ambiguidade entre numeros (CEP vs CPF vs casa).
_LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "name": re.compile(r"nome\s*[:\-]\s*(.+)", re.IGNORECASE),
    "document": re.compile(r"(?:cpf|cnpj|documento|doc)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "postal_code": re.compile(r"cep\s*[:\-]\s*(.+)", re.IGNORECASE),
    "number": re.compile(r"(?:n[uú]mero|numero|num|n[ºo°.])\s*[:\-]\s*(.+)", re.IGNORECASE),
    "complement": re.compile(r"(?:complemento|compl|comp)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "street": re.compile(r"(?:rua|logradouro|endere[cç]o)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "district": re.compile(r"bairro\s*[:\-]\s*(.+)", re.IGNORECASE),
    "city": re.compile(r"(?:cidade|munic[ií]pio)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "state": re.compile(r"(?:uf|estado)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "quantity": re.compile(r"(?:quantidade|qtd|potes|quantos)\s*[:\-]\s*(.+)", re.IGNORECASE),
}

# CPF em texto livre: 11 digitos com ou sem mascara.
_CPF_FREE_RE = re.compile(r"\b\d{3}\.?\s?\d{3}\.?\s?\d{3}-?\s?\d{2}\b")

_FIELD_LABELS_PT = {
    "name": "nome completo",
    "document": "CPF",
    "postal_code": "CEP",
    "number": "numero da casa",
    "street": "rua",
    "district": "bairro",
    "city": "cidade",
    "state": "UF (estado)",
}

# Mensagem de transicao: o LLM emite isto ao entrar em modo checkout. Modelo
# rotulado = maxima precisao de parsing sem tela.


def build_transition_message(quantity: int = 1) -> str:
    """Mensagem de transicao para o modo checkout, confirmando a quantidade.

    Story-041 HIGH-2: confirmar a quantidade aqui da ao cliente a chance de
    corrigir antes do Pix e deixa explicito o valor que sera cobrado, fechando a
    janela em que a quantidade poderia cair para 1 silenciosamente.
    """
    qty = max(1, int(quantity or 1))
    unit = "pote" if qty == 1 else "potes"
    return (
        f"Perfeito! Vou gerar seu Pix de {qty} {unit}. Pra sair certinho e sem "
        "erro, me manda assim (pode copiar e preencher):\n\n"
        "Nome: \n"
        "CPF: \n"
        "CEP: \n"
        "Numero: "
    )


# Compat: mensagem padrao (quantidade 1) para chamadas legadas/testes.
TRANSITION_MESSAGE = build_transition_message(1)


@dataclass
class CheckoutTurn:
    """Resultado de processar uma mensagem no modo checkout.

    ``ready`` -> todos os campos minimos validos e endereco resolvido; o caller
    pode gerar o Pix com ``collected`` (+ ``resolved_address``).
    ``reply`` -> quando NAO ``ready``, a mensagem deterministica a enviar literal
    (pede so o que faltou/ficou invalido).
    """

    ready: bool
    collected: dict[str, Any] = field(default_factory=dict)
    resolved_address: dict[str, str] = field(default_factory=dict)
    reply: str = ""
    invalid_fields: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing (puro)
# ---------------------------------------------------------------------------


def parse_labeled(text: str) -> dict[str, str]:
    """Extrai campos do formato rotulado (``Campo: valor``), linha a linha."""
    parsed: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for field_name, pattern in _LABEL_PATTERNS.items():
            match = pattern.search(line)
            if match:
                value = match.group(1).strip()
                if value:
                    parsed[field_name] = value
    return parsed


def _refine_number(parsed: dict[str, str]) -> None:
    """Separa numero/complemento embutido (ex.: 'Numero: 930 casa 5' -> 930 + casa 5).

    Usa o parser da story-040 para limpar o valor rotulado do numero.
    """
    raw = parsed.get("number", "")
    if not raw:
        return
    nc = parse_free_text_address(raw)
    if nc.get("number"):
        parsed["number"] = nc["number"]
        if nc.get("complement") and not parsed.get("complement"):
            parsed["complement"] = nc["complement"]


def _document_from_free_text(text: str) -> str:
    """Extrai CPF (11 digitos mascarados ou nao) de texto livre — inequivoco."""
    match = _CPF_FREE_RE.search(text or "")
    if match and len(only_digits(match.group(0))) == 11:
        return match.group(0)
    return ""


def parse_message(text: str) -> dict[str, str]:
    """Extrai campos de uma mensagem.

    Se a mensagem tem QUALQUER rotulo, confia nos rotulos (e so refina o numero +
    tenta o CPF, que e inequivoco). Isso evita o erro classico de o parser de
    texto livre capturar digitos do CPF/CEP como 'numero da casa'. Apenas
    mensagens SEM nenhum rotulo passam pelo parser de texto livre completo.
    """
    labeled = parse_labeled(text)
    if labeled:
        _refine_number(labeled)
        if not labeled.get("document"):
            doc = _document_from_free_text(text)
            if doc:
                labeled["document"] = doc
        return labeled

    # Sem rotulos: parser de texto livre completo (story-040) + CPF.
    parsed: dict[str, str] = {}
    free = parse_free_text_address(text)
    for key in ("postal_code", "number", "complement"):
        if free.get(key):
            parsed[key] = free[key]
    doc = _document_from_free_text(text)
    if doc:
        parsed["document"] = doc
    return parsed


# ---------------------------------------------------------------------------
# Validacao por campo (puro)
# ---------------------------------------------------------------------------


def _is_full_name(value: str) -> bool:
    parts = [p for p in (value or "").strip().split() if len(p) >= 2]
    return len(parts) >= 2


def validate_field(field_name: str, value: str) -> bool:
    """True se ``value`` e um valor valido para ``field_name``."""
    value = (value or "").strip()
    if not value:
        return field_name == "complement"  # complemento e opcional
    if field_name == "name":
        return _is_full_name(value)
    if field_name == "document":
        return is_valid_document(value)
    if field_name == "postal_code":
        return len(only_digits(value)) == 8
    if field_name == "number":
        return bool(only_digits(value))
    if field_name == "state":
        return len(value.strip()) == 2
    if field_name in ("street", "district", "city", "complement"):
        return True
    return False


def _normalize_field(field_name: str, value: str) -> str:
    value = (value or "").strip()
    if field_name == "postal_code":
        return only_digits(value)
    if field_name == "document":
        return only_digits(value)
    if field_name == "state":
        return value.upper()
    return value


def merge_collected(existing: dict[str, Any], parsed: dict[str, str]) -> dict[str, Any]:
    """Acumula campos VALIDOS sem nunca sobrescrever um campo ja presente.

    Garante o requisito central: um campo ja coletado (valido) NAO volta a ser
    pedido — so entram no estado valores que passam na validacao, e um campo ja
    no estado e preservado.
    """
    merged = dict(existing)
    for field_name, value in parsed.items():
        if field_name in merged and merged[field_name]:
            continue  # ja temos — nunca sobrescreve/repede
        if validate_field(field_name, value):
            merged[field_name] = _normalize_field(field_name, value)
    return merged


def invalid_attempts(existing: dict[str, Any], parsed: dict[str, str]) -> list[str]:
    """Campos que vieram nesta mensagem mas falharam a validacao (e ainda nao
    estavam coletados) — para avisar o cliente nominalmente."""
    invalid: list[str] = []
    for field_name, value in parsed.items():
        if existing.get(field_name):
            continue
        if field_name == "complement":
            continue
        if not validate_field(field_name, value):
            invalid.append(field_name)
    return invalid


def pending_required(collected: dict[str, Any]) -> list[str]:
    """Campos minimos ainda nao coletados."""
    return [f for f in REQUIRED_FIELDS if not collected.get(f)]


# ---------------------------------------------------------------------------
# Mensagens deterministicas (puro)
# ---------------------------------------------------------------------------


def _join_pt(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} e {items[1]}"
    return ", ".join(items[:-1]) + f" e {items[-1]}"


def build_reply(pending: list[str], invalid: list[str]) -> str:
    """Mensagem pedindo SO o que faltou/ficou invalido — nunca 'manda tudo de novo'."""
    parts: list[str] = []
    if invalid:
        invalid_labels = [_FIELD_LABELS_PT.get(f, f) for f in invalid]
        if "document" in invalid:
            parts.append(
                "o CPF informado nao parece valido, pode conferir os numeros?"
            )
            invalid_labels = [l for l in invalid_labels if l != "CPF"]
        if invalid_labels:
            parts.append(f"o campo {_join_pt(invalid_labels)} ficou invalido")
    if pending:
        pending_labels = [_FIELD_LABELS_PT.get(f, f) for f in pending]
        parts.append(f"faltou {_join_pt(pending_labels)}")
    if not parts:
        return ""
    return "Quase la! So preciso que voce confira: " + "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Avanco do fluxo (async — resolve CEP via ViaCEP)
# ---------------------------------------------------------------------------


async def advance_checkout(
    text: str,
    collected: Optional[dict[str, Any]] = None,
    *,
    viacep_timeout: float = 3.0,
) -> CheckoutTurn:
    """Processa uma mensagem no modo checkout e retorna o proximo passo.

    1. Parseia (rotulado + fallback) e acumula campos validos (nunca repede).
    2. Se faltam campos minimos -> pede so o que falta/ficou invalido.
    3. Com os minimos completos -> resolve o endereco (ViaCEP). Se o ViaCEP nao
       trouxer rua/bairro/cidade/UF, pede esses campos rotulados (fallback).
    4. Tudo pronto -> ``ready=True`` com ``collected`` e ``resolved_address``.
    """
    state = dict(collected or {})
    parsed = parse_message(text)
    invalid = invalid_attempts(state, parsed)
    state = merge_collected(state, parsed)

    pending = pending_required(state)
    if pending or invalid:
        reply = build_reply(pending, invalid)
        return CheckoutTurn(ready=False, collected=state, reply=reply, invalid_fields=invalid)

    # Minimos completos: resolve o endereco a partir do CEP (FR-4).
    resolved = await resolve_delivery_address(
        {
            "postal_code": state["postal_code"],
            "number": state["number"],
            "complement": state.get("complement", ""),
        },
        timeout=viacep_timeout,
    )

    # Se o ViaCEP nao resolveu rua/bairro/cidade/UF, pede esses campos (sem
    # repedir CEP/numero, que ja temos).
    addr_pending = [
        f for f in ADDRESS_FALLBACK_FIELDS if not (resolved.get(f) or state.get(f))
    ]
    if addr_pending:
        for f in ADDRESS_FALLBACK_FIELDS:
            if state.get(f) and not resolved.get(f):
                resolved[f] = state[f]
        still_pending = [f for f in ADDRESS_FALLBACK_FIELDS if not resolved.get(f)]
        if still_pending:
            reply = build_reply(still_pending, [])
            return CheckoutTurn(
                ready=False, collected=state, resolved_address=resolved, reply=reply
            )

    return CheckoutTurn(ready=True, collected=state, resolved_address=resolved)
