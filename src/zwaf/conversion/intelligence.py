"""Deterministic conversion intelligence for WhatsApp sales flows."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum


class Sentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    PRICE_OBJECTION = "PRICE_OBJECTION"
    FRUSTRATED = "FRUSTRATED"
    ANGRY = "ANGRY"
    HEALTH_RISK = "HEALTH_RISK"


class BuyingIntent(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConversionAction(str, Enum):
    ANSWER_QUESTION = "ANSWER_QUESTION"
    HANDLE_OBJECTION = "HANDLE_OBJECTION"
    ASK_FOLLOWUP = "ASK_FOLLOWUP"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    RECOVER_PAYMENT = "RECOVER_PAYMENT"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    TRANSFER_AGENT = "TRANSFER_AGENT"


@dataclass(frozen=True)
class LeadSignal:
    sentiment: Sentiment
    buying_intent: BuyingIntent
    action: ConversionAction
    confidence: float
    should_send_payment_link: bool
    reasons: list[str] = field(default_factory=list)
    product_hint: str | None = None
    objection: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sentiment"] = self.sentiment.value
        data["buying_intent"] = self.buying_intent.value
        data["action"] = self.action.value
        return data


_HIGH_INTENT_PATTERNS = [
    r"^\s*sim\s*$",
    r"\bsim,?\s*(pode|quero|confirmo)\b",
    r"\bconfirmo\b",
    r"\bconfirmado\b",
    r"\bpode gerar\b",
    r"\bpode mandar\b",
    r"\bpode enviar\b",
    r"\bgera(?:r)? o link\b",
    r"\bgerar link\b",
    r"\bgerar o link\b",
    r"\bme envia o link\b",
    r"\bquero comprar\b",
    r"\bcomprar agora\b",
    r"\bmanda(?:r)? o link\b",
    r"\bme passa o link\b",
    r"\bpode mandar\b",
    r"\bfechar pedido\b",
    r"\bvou querer\b",
    r"\bquero o pix\b",
    r"\bme envia o pix\b",
    # Story-042: intencao explicita de pagar no cartao / parcelar tambem fecha.
    r"\bquero parcelar\b",
    r"\bpagar (?:no |com |de |em )?cart[aã]o\b",
    r"\bpagar parcelad[oa]\b",
    r"\blink (?:do |no )?cart[aã]o\b",
]

_MEDIUM_INTENT_PATTERNS = [
    r"\bquanto custa\b",
    r"\bqual o valor\b",
    r"\bpreco\b",
    r"\bpreço\b",
    r"\btem frete\b",
    r"\bcomo pago\b",
]

_PRICE_OBJECTION_PATTERNS = [
    r"\bcaro\b",
    r"\bdesconto\b",
    r"\bmelhor preco\b",
    r"\bmelhor preço\b",
    r"\bmuito puxado\b",
]

_ANGER_PATTERNS = [
    r"\bgolpe\b",
    r"\bprocon\b",
    r"\bprocesso\b",
    r"\benganad[ao]\b",
    r"\bquero devolver\b",
    r"\breembolso\b",
]

_FRUSTRATION_PATTERNS = [
    r"\bnao chegou\b",
    r"\bnão chegou\b",
    r"\batrasou\b",
    r"\bnao consigo pagar\b",
    r"\bnão consigo pagar\b",
    r"\blink expirou\b",
    r"\berro no pix\b",
]

_HEALTH_RISK_PATTERNS = [
    r"\befeito colateral\b",
    r"\bpassando mal\b",
    r"\balergia\b",
    r"\bdor forte\b",
    r"\breacao\b",
    r"\breação\b",
]


def analyze_message(message: str, tenant_id: str = "", agent_name: str = "") -> LeadSignal:
    """Classify a WhatsApp message into conversion signals."""
    normalized = _normalize(message)
    product_hint = _product_hint(normalized)

    if product_hint == "alpha-pulse" and tenant_id == "livia-raiz-vital":
        return LeadSignal(
            sentiment=Sentiment.NEUTRAL,
            buying_intent=BuyingIntent.MEDIUM,
            action=ConversionAction.TRANSFER_AGENT,
            confidence=0.9,
            should_send_payment_link=False,
            reasons=["Alpha Pulse deve ser atendido pelo consultor correto"],
            product_hint=product_hint,
        )

    if _matches(normalized, _HEALTH_RISK_PATTERNS):
        return LeadSignal(
            sentiment=Sentiment.HEALTH_RISK,
            buying_intent=BuyingIntent.NONE,
            action=ConversionAction.ESCALATE_HUMAN,
            confidence=0.95,
            should_send_payment_link=False,
            reasons=["Risco de saude exige escalacao humana"],
            product_hint=product_hint,
        )

    if _matches(normalized, _ANGER_PATTERNS):
        return LeadSignal(
            sentiment=Sentiment.ANGRY,
            buying_intent=BuyingIntent.NONE,
            action=ConversionAction.ESCALATE_HUMAN,
            confidence=0.92,
            should_send_payment_link=False,
            reasons=["Raiva ou reclamacao grave detectada"],
            product_hint=product_hint,
        )

    if _matches(normalized, _FRUSTRATION_PATTERNS):
        action = ConversionAction.RECOVER_PAYMENT if "pix" in normalized or "link" in normalized else ConversionAction.ESCALATE_HUMAN
        return LeadSignal(
            sentiment=Sentiment.FRUSTRATED,
            buying_intent=BuyingIntent.LOW,
            action=action,
            confidence=0.86,
            should_send_payment_link=False,
            reasons=["Frustracao operacional detectada"],
            product_hint=product_hint,
        )

    if _matches(normalized, _PRICE_OBJECTION_PATTERNS):
        return LeadSignal(
            sentiment=Sentiment.PRICE_OBJECTION,
            buying_intent=BuyingIntent.MEDIUM,
            action=ConversionAction.HANDLE_OBJECTION,
            confidence=0.82,
            should_send_payment_link=False,
            reasons=["Objecao de preco antes do checkout"],
            product_hint=product_hint,
            objection="price",
        )

    if _matches(normalized, _HIGH_INTENT_PATTERNS):
        return LeadSignal(
            sentiment=Sentiment.POSITIVE,
            buying_intent=BuyingIntent.HIGH,
            action=ConversionAction.SEND_PAYMENT_LINK,
            confidence=0.9,
            should_send_payment_link=True,
            reasons=["Intencao clara de compra"],
            product_hint=product_hint,
        )

    if _matches(normalized, _MEDIUM_INTENT_PATTERNS):
        return LeadSignal(
            sentiment=Sentiment.NEUTRAL,
            buying_intent=BuyingIntent.MEDIUM,
            action=ConversionAction.ASK_FOLLOWUP,
            confidence=0.72,
            should_send_payment_link=False,
            reasons=["Interesse comercial sem confirmacao de compra"],
            product_hint=product_hint,
        )

    return LeadSignal(
        sentiment=Sentiment.NEUTRAL,
        buying_intent=BuyingIntent.LOW if product_hint else BuyingIntent.NONE,
        action=ConversionAction.ANSWER_QUESTION,
        confidence=0.6,
        should_send_payment_link=False,
        reasons=["Sem sinal suficiente para checkout"],
        product_hint=product_hint,
    )


def decide_payment_link(product_id: str, buying_intent_evidence: str, tenant_id: str = "") -> LeadSignal:
    """Gate payment links using explicit buying-intent evidence."""
    signal = analyze_message(buying_intent_evidence, tenant_id=tenant_id)
    product_slug = product_id.replace("_", "-").lower()

    if tenant_id == "livia-raiz-vital" and product_slug.startswith("alpha-pulse"):
        return LeadSignal(
            sentiment=Sentiment.NEUTRAL,
            buying_intent=BuyingIntent.MEDIUM,
            action=ConversionAction.TRANSFER_AGENT,
            confidence=0.95,
            should_send_payment_link=False,
            reasons=["Livia nao vende Alpha Pulse"],
            product_hint="alpha-pulse",
        )

    if signal.should_send_payment_link:
        return signal

    return LeadSignal(
        sentiment=signal.sentiment,
        buying_intent=signal.buying_intent,
        action=signal.action,
        confidence=signal.confidence,
        should_send_payment_link=False,
        reasons=[*signal.reasons, "Link bloqueado ate intencao de compra explicita"],
        product_hint=signal.product_hint,
        objection=signal.objection,
    )


def _normalize(message: str) -> str:
    return " ".join(message.lower().strip().split())


def _matches(message: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, message) for pattern in patterns)


def _product_hint(message: str) -> str | None:
    if "alpha pulse" in message or "alpha-pulse" in message:
        return "alpha-pulse"
    if "new woman" in message or "new-woman" in message:
        return "new-woman"
    return None
