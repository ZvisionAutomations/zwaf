"""Livia follow-up policy and lead temperature classification."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from zwaf.conversion.checkout_policy import is_critical_complaint, is_opt_out_message


class LeadTemperature(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    RISK = "risk"


class FollowupStage(str, Enum):
    POST_OFFER = "post_offer"
    CHECKOUT_INCOMPLETE = "checkout_incomplete"
    POST_LINK = "post_link"
    REPURCHASE = "repurchase"


@dataclass(frozen=True)
class LeadTemperatureResult:
    temperature: LeadTemperature
    strong_signals: list[str] = field(default_factory=list)
    final_signals: list[str] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FollowupContact:
    sequence: int
    delay_hours: int
    template_id: str
    text: str


@dataclass(frozen=True)
class FollowupPlan:
    allowed: bool
    reason: str
    temperature: LeadTemperature
    stage: FollowupStage
    max_contacts: int
    contacts: list[FollowupContact] = field(default_factory=list)


STRONG_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "diagnosis_real_pain": (
        r"\bdor\b",
        r"\bsintoma",
        r"\bcalor(?:es)?\b",
        r"\bsono\b",
        r"\bmenopausa\b",
        r"\bclimaterio\b",
        r"\btpm\b",
    ),
    "asked_price": (r"\bpreco\b", r"\bquanto custa\b", r"\bqual o valor\b", r"\bvalor\b"),
    "asked_payment": (r"\bpix\b", r"\bcartao\b", r"\bboleto\b", r"\bcomo pago\b"),
    "selected_quantity": (r"\b\d+\s*potes?\b", r"\bquero\s+\d+\b"),
    "asked_shipping": (r"\bfrete\b", r"\bprazo\b", r"\bentrega\b", r"\bcep\b"),
    "sent_checkout_data": (r"\bcpf\b", r"\bcnpj\b", r"\bendereco\b", r"\bcep\b"),
    "buying_words": (r"\bquero\b", r"\bvou comprar\b", r"\bmanda\b", r"\bmanda o link\b"),
}

FINAL_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "requested_link": (r"\blink\b", r"\bmanda o link\b", r"\bgerar o link\b"),
    "selected_payment_method": (r"\bpix\b", r"\bcartao\b", r"\bboleto\b"),
    "sent_checkout_data": (r"\bcpf\b", r"\bcnpj\b", r"\bendereco\b", r"\bcep\b"),
}

RISK_PATTERNS: dict[str, tuple[str, ...]] = {
    "medical_risk": (
        r"\bremedio\b",
        r"\bmedicacao\b",
        r"\bmedicamento\b",
        r"\breacao\b",
        r"\bef(e|ei)to colateral\b",
        r"\balergia\b",
        r"\bpassando mal\b",
        r"\bgravida\b",
        r"\bamamentando\b",
    ),
}

APPROVED_FOLLOWUP_TEMPLATES: dict[FollowupStage, tuple[str, ...]] = {
    FollowupStage.POST_OFFER: (
        "Ficou alguma duvida sobre valores ou sobre como usar o New Woman?",
        "Se quiser comecar com menor risco, 1 pote ja e um bom primeiro passo.",
        "O que mais pega hoje: valor, seguranca, pagamento ou entrega?",
        "Se o frete gratis ainda estiver ativo, posso te ajudar a aproveitar sem pressa.",
        "Quer que eu encerre por aqui ou prefere que eu te ajude a seguir?",
    ),
    FollowupStage.CHECKOUT_INCOMPLETE: (
        "Faltou algum dado do pedido ou ficou alguma duvida para finalizar?",
        "Podemos comecar com 1 pote e manter o pedido simples.",
        "Se travou por pagamento, entrega ou seguranca, me fala qual ponto.",
        "Se o frete gratis ainda estiver ativo, posso recalcular o melhor caminho.",
        "Esse e meu ultimo retorno: quer seguir ou encerro sem problema?",
    ),
    FollowupStage.POST_LINK: (
        "Conseguiu acessar o link ou apareceu alguma duvida sobre o pagamento?",
        "Se preferir, posso te orientar para fechar com 1 pote primeiro.",
        "Se o link travou por Pix, cartao ou boleto, me fala que eu te ajudo.",
        "Se o frete gratis ainda estiver ativo, vale conferir antes de pagar.",
        "Quer que eu acompanhe esse pedido ou posso encerrar por aqui?",
    ),
    FollowupStage.REPURCHASE: (
        "Como foi sua experiencia com o New Woman ate aqui?",
        "Se quiser manter o uso, posso te ajudar a escolher a quantidade com calma.",
        "Se algo te deixou em duvida na recompra, me conta antes de decidir.",
    ),
}

HOT_DELAY_HOURS = (1, 24, 48, 96, 168)


def classify_lead_temperature(messages: list[str] | str) -> LeadTemperatureResult:
    """Classify lead temperature using approved deterministic signals."""
    text = _normalize(" ".join(messages) if isinstance(messages, list) else messages)

    risk_signals = _matched_signal_names(text, RISK_PATTERNS)
    if risk_signals or is_critical_complaint(text):
        return LeadTemperatureResult(LeadTemperature.RISK, risk_signals=risk_signals or ["critical_complaint"])

    final_signals = _matched_signal_names(text, FINAL_SIGNAL_PATTERNS)
    strong_signals = _matched_signal_names(text, STRONG_SIGNAL_PATTERNS)

    if final_signals or len(strong_signals) >= 2:
        return LeadTemperatureResult(LeadTemperature.HOT, strong_signals, final_signals)
    if len(strong_signals) == 1:
        return LeadTemperatureResult(LeadTemperature.WARM, strong_signals, final_signals)
    return LeadTemperatureResult(LeadTemperature.COLD, strong_signals, final_signals)


def build_followup_plan(
    *,
    messages: list[str] | str,
    stage: FollowupStage | str,
    contacts_already_sent: int = 0,
    dry_or_resistant: bool = False,
    temperature_override: LeadTemperature | str | None = None,
) -> FollowupPlan:
    """Return the allowed commercial follow-up plan without sending anything.

    ``temperature_override`` lets a caller (e.g. the commercial follow-up engine)
    pass the lead temperature persisted at enrollment instead of re-deriving it
    from message text. This is the single source of truth for cadence/limits and
    avoids the fragile round-trip through synthetic messages (story-065 HIGH-4).
    """
    stage_enum = FollowupStage(stage)
    temperature = (
        LeadTemperature(temperature_override)
        if temperature_override is not None
        else classify_lead_temperature(messages).temperature
    )
    normalized = _normalize(" ".join(messages) if isinstance(messages, list) else messages)

    if is_opt_out_message(normalized):
        return FollowupPlan(False, "opt_out", temperature, stage_enum, 0)
    if temperature is LeadTemperature.RISK:
        return FollowupPlan(False, "medical_risk", temperature, stage_enum, 0)

    max_contacts = _max_contacts(temperature, normalized, dry_or_resistant)
    remaining = max(0, max_contacts - max(0, contacts_already_sent))
    if remaining == 0:
        return FollowupPlan(False, "limit_reached", temperature, stage_enum, max_contacts)

    templates = APPROVED_FOLLOWUP_TEMPLATES[stage_enum]
    contacts: list[FollowupContact] = []
    end_index = min(max_contacts, len(HOT_DELAY_HOURS), contacts_already_sent + remaining)
    for index in range(contacts_already_sent, end_index):
        template_index = min(index, len(templates) - 1)
        contacts.append(
            FollowupContact(
                sequence=index + 1,
                delay_hours=HOT_DELAY_HOURS[index],
                template_id=f"{stage_enum.value}_{template_index + 1}",
                text=templates[template_index],
            )
        )

    return FollowupPlan(True, "scheduled", temperature, stage_enum, max_contacts, contacts)


def _max_contacts(temperature: LeadTemperature, text: str, dry_or_resistant: bool) -> int:
    if dry_or_resistant:
        return 2
    if temperature is LeadTemperature.HOT:
        if _price_only(text):
            return 3
        return 5
    if temperature is LeadTemperature.WARM:
        return 3
    if temperature is LeadTemperature.COLD:
        return 1
    return 0


def _price_only(text: str) -> bool:
    strong = _matched_signal_names(text, STRONG_SIGNAL_PATTERNS)
    final = _matched_signal_names(text, FINAL_SIGNAL_PATTERNS)
    return strong == ["asked_price"] and not final


def _matched_signal_names(text: str, patterns_by_name: dict[str, tuple[str, ...]]) -> list[str]:
    return [
        name
        for name, patterns in patterns_by_name.items()
        if any(re.search(pattern, text) for pattern in patterns)
    ]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()
