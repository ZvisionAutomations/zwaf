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

# ---------------------------------------------------------------------------
# pushName (story-068): sanitizacao + confirmacao de nome declarativo
# ---------------------------------------------------------------------------

# Conectores que NAO levam inicial maiuscula em nome proprio PT-BR.
_NAME_CONNECTORS = frozenset({"de", "da", "do", "das", "dos", "e"})


def _capitalize_segments(word: str) -> str:
    """Capitaliza a 1a letra de cada segmento de letras (separados por ``-``/``'``),
    minusculizando o resto. Cobre nomes compostos: 'Ana-Clara', \"D'Avila\"."""
    return re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
        word,
        flags=re.UNICODE,
    )


def _title_case_name(text: str) -> str:
    """Title Case PT-BR: capitaliza cada palavra (e cada segmento de nome composto),
    mas mantem conectores em caixa baixa (exceto se forem a primeira palavra)."""
    words = text.split()
    out: list[str] = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i > 0 and lower in _NAME_CONNECTORS:
            out.append(lower)
        else:
            out.append(_capitalize_segments(word))
    return " ".join(out)


def sanitize_name(raw: str) -> str:
    """Sanitiza um nome declarativo (ex.: ``pushName`` do WhatsApp) para exibicao
    e para uso em cobranca (story-068).

    - remove emojis/simbolos e caracteres de controle (``str.isalpha`` e
      unicode-aware: cobre acentos e descarta emoji/pictograma/digito);
    - mantem apenas letras, espacos e a pontuacao tipica de nome (``- ' .``);
    - colapsa espacos, apara e normaliza a caixa (Title Case PT-BR).

    Retorna ``""`` quando nada utilizavel sobra (ex.: nome so de emoji/numero) —
    o caller cai no fluxo de pedir o nome.
    """
    if not raw:
        return ""
    cleaned = "".join(
        ch if (ch.isalpha() or ch.isspace() or ch in "-'.") else " "
        for ch in raw
    )
    text = re.sub(r"\s+", " ", cleaned).strip(" .-'")
    if not text or not any(ch.isalpha() for ch in text):
        return ""
    return _title_case_name(text)


# Confirmacao do nome pre-preenchido: aceita o "sim" em varias formas. Se houver
# qualquer marca de negacao, NAO conta como confirmacao (cliente quer outro nome).
_NAME_NEGATIVE_RE = re.compile(
    r"\b(n[aã]o|nops?|negativ[oa]|errad[oa]|outr[oa]|muda(?:r)?|troca(?:r)?|nem)\b",
    re.IGNORECASE,
)
_NAME_AFFIRMATIVE_RE = re.compile(
    r"\b(sim|isso|pode|claro|perfeito|exat[oa]|exatamente|correto|cert[oa]|"
    r"confirm[oa]|confirmad[oa]|ok|okay|blz|beleza|positivo|aham|uhum|aprovo|"
    r"manda|registra(?:r)?|esse(?:\s+mesmo)?|sou\s+eu)\b",
    re.IGNORECASE,
)


def is_affirmative_name_confirmation(text: str) -> bool:
    """True se ``text`` confirma o nome proposto (sem negacao). Conservador: na
    duvida retorna False e o fluxo segue pedindo/confirmando o nome."""
    value = (text or "").strip()
    if not value:
        return False
    if _NAME_NEGATIVE_RE.search(value):
        return False
    return bool(_NAME_AFFIRMATIVE_RE.search(value))


def build_name_confirm_message(push_name: str) -> str:
    """Pergunta de 1 toque confirmando o nome trazido pelo WhatsApp (story-068)."""
    name = (push_name or "").strip()
    return (
        f"Posso registrar o pedido em nome de *{name}*? "
        "(se preferir outro nome, e so me dizer)"
    )


# Mensagem de transicao: o LLM emite isto ao entrar em modo checkout. Modelo
# rotulado = maxima precisao de parsing sem tela.


def build_transition_message(
    quantity: int = 1,
    billing_type: str = "PIX",
    known_name: str = "",
) -> str:
    """Mensagem de transicao para o modo checkout, confirmando a quantidade.

    Story-041 HIGH-2: confirmar a quantidade aqui da ao cliente a chance de
    corrigir antes do pagamento e deixa explicito o valor que sera cobrado,
    fechando a janela em que a quantidade poderia cair para 1 silenciosamente.

    Story-042: a mesma coleta serve para Pix e cartao; so muda a palavra do meio
    ("Pix" vs "link de pagamento no cartao") para alinhar a expectativa.

    Story-068: quando ``known_name`` ja foi confirmado (via ``pushName``), o
    formulario OMITE a linha ``Nome:`` — pede so o que falta (CPF/CEP/Numero).
    """
    qty = max(1, int(quantity or 1))
    unit = "pote" if qty == 1 else "potes"
    _bt = (billing_type or "PIX").upper()
    meio = (
        "link de pagamento no cartao" if _bt == "CREDIT_CARD"
        else "boleto" if _bt == "BOLETO"  # story-069
        else "Pix"
    )
    name = (known_name or "").strip()
    if name:
        return (
            f"Perfeito! Vou gerar seu {meio} de {qty} {unit} em nome de *{name}*. "
            "Pra sair certinho e sem erro, me manda assim (pode copiar e preencher):"
            "\n\n"
            "CPF: \n"
            "CEP: \n"
            "Numero: "
        )
    return (
        f"Perfeito! Vou gerar seu {meio} de {qty} {unit}. Pra sair certinho e sem "
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


# Linha que parece nome proprio: 2+ palavras so de letras/acentos (sem digitos).
_NAME_LINE_RE = re.compile(r"^[A-Za-zÀ-ÿ]{2,}(?:\s+[A-Za-zÀ-ÿ.]{1,})+$")

# Palavras que indicam que a linha NAO e um nome (comandos, rotulos, logradouros).
# Evita capturar "quero pagar agora" ou "rua das flores" como nome do cliente.
_NAME_STOPWORDS = frozenset({
    "quero", "pagar", "manda", "mandar", "envia", "enviar", "link", "pix", "cartao",
    "cartão", "credito", "crédito", "parcelar", "parcelado", "sim", "nao", "não",
    "ola", "olá", "oi", "obrigado", "obrigada", "fechar", "pedido", "comprar",
    "compra", "potes", "pote", "quanto", "custa", "valor", "preco", "preço",
    "rua", "avenida", "av", "alameda", "travessa", "rodovia", "estrada", "praca",
    "praça", "bairro", "cidade", "numero", "número", "complemento", "compl",
    "cep", "nome", "cpf", "cnpj", "uf", "estado", "endereco", "endereço",
})


def _name_from_free_text(text: str) -> str:
    """Extrai um nome completo de texto livre (sem rotulo).

    Caso de dados sem rotulo: o cliente copia os valores do formulario mas NAO os
    rotulos ("Joao Carlos Pereira" numa linha solta). Sem isso o nome nunca
    e capturado e o checkout entra em loop de "faltou nome completo".

    Heuristica conservadora: uma linha so de letras com 2+ palavras e sem
    nenhuma stopword (comando/rotulo/logradouro). Retorna a primeira que casar.
    """
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not _NAME_LINE_RE.match(line):
            continue
        words = line.lower().split()
        if any(w in _NAME_STOPWORDS for w in words):
            continue
        if len([w for w in line.split() if len(w) >= 2]) >= 2:
            return line
    return ""


# story-063: rotulos cujas linhas NAO devem ser varridas atras do numero da casa
# (carregam digitos que confundem — CPF/CEP — ou ja sao o proprio numero).
_NUMBER_SCAN_SKIP_LABELS = ("name", "document", "postal_code", "number", "quantity")


def _number_from_unlabeled_lines(text: str, labeled: dict[str, str]) -> dict[str, str]:
    """Recupera numero/complemento de linhas SEM rotulo (ex.: a linha do endereco).

    Caso real (Kaue, story-063): a cliente manda os campos rotulados mas escreve o
    endereco numa linha solta ("Rua X 52, casa 97"). Sem o rotulo "Numero:", o
    numero nunca era extraido e o checkout pedia "faltou numero da casa" mesmo com
    ele presente. Aqui varremos apenas linhas que NAO casam rotulos de
    CPF/CEP/numero e que NAO contem os digitos do CPF/CEP ja coletados — evitando
    confundir esses digitos com o numero da casa (FR-5).
    """
    doc_digits = only_digits(labeled.get("document", ""))
    cep_digits = only_digits(labeled.get("postal_code", ""))
    skip_patterns = [_LABEL_PATTERNS[k] for k in _NUMBER_SCAN_SKIP_LABELS]
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(pattern.search(line) for pattern in skip_patterns):
            continue
        line_digits = only_digits(line)
        if doc_digits and doc_digits in line_digits:
            continue
        if len(cep_digits) == 8 and cep_digits in line_digits:
            continue
        parsed = parse_free_text_address(line)
        if parsed.get("number"):
            return parsed
    return {}


# story-074 BUG-1: extracao de street/district/city/state de texto livre
# NAO-rotulado. Fallback para quando o ViaCEP nao resolve: sem isto, mesmo o
# cliente tendo escrito rua/bairro/cidade/UF, o checkout pedia esses campos em
# LOOP porque o parser nunca os populava. ViaCEP segue fonte de verdade (FR-5).
_UF_SET = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
})
# Linha que abre com prefixo de logradouro (rua/av/travessa/...).
_LOGRADOURO_RE = re.compile(
    r"^(?:rua|r|avenida|av|travessa|tv|alameda|al|rodovia|rod|estrada|estr|"
    r"pra[cç]a|praca|largo|viela|quadra|via|servid[aã]o)\b\.?\s+\S",
    re.IGNORECASE,
)
# UF de 2 letras no fim da linha ("Sao Paulo - SP", "Sao Paulo/SP", "Sao Paulo SP").
_TRAILING_UF_RE = re.compile(r"[\s,/\-]+([A-Za-z]{2})\s*$")
# Cauda de numero da casa numa linha de logradouro ("Rua X 930" -> "Rua X").
_HOUSE_NUMBER_TAIL_RE = re.compile(r"[\s,]+n?[ºo°.]?\s*\d{1,6}\b.*$", re.IGNORECASE)


def _address_parts_from_free_text(text: str, labeled: dict[str, str]) -> dict[str, str]:
    """Extrai street/district/city/state de texto livre (story-074 BUG-1).

    Heuristica conservadora, linha a linha, ignorando as linhas de nome/CPF/CEP/
    rotuladas:
    - ``street``: linha que abre com prefixo de logradouro (sem a cauda do numero);
    - ``state``: token de 2 letras que e uma UF valida (isolado ou no fim da linha);
    - ``district``/``city``: linhas textuais restantes (1a -> bairro, ultima -> cidade).

    Nunca sobrescreve um campo ja rotulado. So preenche o que faltou — alimenta o
    fallback de ``advance_checkout`` quando o ViaCEP nao resolve.
    """
    result: dict[str, str] = {}
    name = (labeled.get("name") or _name_from_free_text(text)).strip()
    doc_digits = only_digits(labeled.get("document", "")) or only_digits(
        _document_from_free_text(text)
    )
    cep_digits = only_digits(labeled.get("postal_code", "")) or only_digits(
        parse_free_text_address(text).get("postal_code", "")
    )
    label_patterns = list(_LABEL_PATTERNS.values())
    leftover: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(pattern.search(line) for pattern in label_patterns):
            continue  # linha rotulada — tratada pelo parse rotulado
        if name and line == name:
            continue
        line_digits = only_digits(line)
        is_logradouro = bool(_LOGRADOURO_RE.match(line))
        if doc_digits and doc_digits in line_digits and not is_logradouro:
            continue  # linha do CPF
        if is_logradouro:
            street = _HOUSE_NUMBER_TAIL_RE.sub("", line).strip(" ,.-")
            if street:
                result.setdefault("street", street)
            continue
        if len(cep_digits) == 8 and cep_digits in line_digits:
            continue  # linha do CEP
        if line.upper() in _UF_SET:
            result.setdefault("state", line.upper())
            continue
        match = _TRAILING_UF_RE.search(line)
        if match and match.group(1).upper() in _UF_SET:
            result.setdefault("state", match.group(1).upper())
            line = line[: match.start()].strip(" ,/-")
        if not line or not any(ch.isalpha() for ch in line):
            continue
        leftover.append(line)
    # So infere bairro/cidade quando a mensagem REALMENTE parece um endereco
    # (tem CEP, logradouro ou UF) — evita que chatter solto ("obrigada!", uma
    # pergunta) vire cidade/bairro no estado do checkout.
    has_address_signal = (
        len(cep_digits) == 8 or "street" in result or "state" in result
    )
    if leftover and has_address_signal:
        if len(leftover) == 1:
            result.setdefault("city", leftover[0])
        else:
            result.setdefault("district", leftover[0])
            result.setdefault("city", leftover[-1])
    return result


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
        if not labeled.get("name"):
            name = _name_from_free_text(text)
            if name:
                labeled["name"] = name
        # story-063: numero embutido em linha de endereco NAO rotulada
        # (ex.: "Rua X 52, casa 97"). Sem isto, mensagem rotulada sem "Numero:"
        # entrava em loop de "faltou numero da casa".
        if not labeled.get("number"):
            recovered = _number_from_unlabeled_lines(text, labeled)
            if recovered.get("number"):
                labeled["number"] = recovered["number"]
                if recovered.get("complement") and not labeled.get("complement"):
                    labeled["complement"] = recovered["complement"]
        # story-074 BUG-1: endereco escrito em linha solta (sem rotulo Rua:/Cidade:)
        # popula street/district/city/state para o fallback (ViaCEP fonte de verdade).
        for key, value in _address_parts_from_free_text(text, labeled).items():
            if value and not labeled.get(key):
                labeled[key] = value
        return labeled

    # Sem rotulos: parser de texto livre completo (story-040) + CPF + nome.
    parsed: dict[str, str] = {}
    free = parse_free_text_address(text)
    for key in ("postal_code", "number", "complement"):
        if free.get(key):
            parsed[key] = free[key]
    doc = _document_from_free_text(text)
    if doc:
        parsed["document"] = doc
    name = _name_from_free_text(text)
    if name:
        parsed["name"] = name
    # story-074 BUG-1: street/district/city/state do texto livre nao-rotulado.
    for key, value in _address_parts_from_free_text(text, parsed).items():
        if value and not parsed.get(key):
            parsed[key] = value
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
            invalid_labels = [label for label in invalid_labels if label != "CPF"]
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

    # story-074 BUG-1: quando o ViaCEP nao trouxe um campo de endereco mas o
    # cliente o escreveu em texto livre (ja em state), usa o do cliente. So pede
    # o que continuar faltando — sem repedir CEP/numero, que ja temos.
    for f in ADDRESS_FALLBACK_FIELDS:
        if state.get(f) and not resolved.get(f):
            resolved[f] = state[f]

    addr_pending = [f for f in ADDRESS_FALLBACK_FIELDS if not resolved.get(f)]
    if addr_pending:
        reply = build_reply(addr_pending, [])
        return CheckoutTurn(
            ready=False, collected=state, resolved_address=resolved, reply=reply
        )

    return CheckoutTurn(ready=True, collected=state, resolved_address=resolved)


# ---------------------------------------------------------------------------
# story-073: pergunta/duvida durante a coleta de checkout
# ---------------------------------------------------------------------------

# Marcadores interrogativos fortes numa duvida de cliente durante a coleta.
# Conservador de proposito: imperativos ambiguos ("pode mandar", "manda") NAO
# entram — uma pergunta genuina sem essas palavras ainda e pega pela regra do "?".
_QUESTION_WORDS_RE = re.compile(
    r"\b(como|quando|qual|quais|quanto|quantos|quantas|onde|cad[eê]|"
    r"porqu[eê]|por\s?que|pra\s?que|posso|funciona|serve|efeito|colateral|"
    r"contraindica|contra\s?indica|demora|entrega|garantia|validade|vence|"
    r"seguro|gravida|amamenta)\b",
    re.IGNORECASE,
)


def message_has_checkout_data(text: str) -> bool:
    """True se a mensagem carrega algum dado de checkout (CPF/CEP/numero da casa).

    Usado para NAO confundir uma mensagem com dados (mesmo que tenha um '?') com
    uma pergunta pura (AC-3 da story-073).
    """
    parsed = parse_message(text or "")
    return any(parsed.get(field_name) for field_name in ("document", "postal_code", "number"))


def is_checkout_question(text: str) -> bool:
    """True se a mensagem e uma PERGUNTA/duvida (nao um dado de checkout) — story-073.

    Conservador: se a mensagem carrega dado de checkout (CPF/CEP/numero), e
    tratada como coleta (AC-3), nunca como pergunta. Caso contrario, considera
    pergunta quando termina em '?' ou contem um marcador interrogativo.
    """
    value = (text or "").strip()
    if not value:
        return False
    if message_has_checkout_data(value):
        return False
    if value.endswith("?"):
        return True
    return bool(_QUESTION_WORDS_RE.search(value))


def build_checkout_resume_hint(collected: dict[str, Any]) -> str:
    """Retomada gentil da coleta apos responder uma duvida (story-073).

    Pede so o que ainda falta — nunca repete secamente "preciso dos dados".
    """
    pending = pending_required(collected or {})
    if not pending:
        return "Quando quiser, e so confirmar que eu ja finalizo seu pedido."
    labels = [_FIELD_LABELS_PT.get(field_name, field_name) for field_name in pending]
    return f"Quando puder, me manda {_join_pt(labels)} que eu finalizo seu pedido."
