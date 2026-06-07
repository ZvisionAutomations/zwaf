"""Commercial daily report for Fernando without customer PII."""
from __future__ import annotations

import re
from typing import Any

from zwaf.conversion.funnel_events import contains_sensitive_value
from zwaf.tools.notifications import notify_fernando

UNAVAILABLE = "indisponivel"


def format_commercial_daily_report(metrics: dict[str, Any]) -> str:
    """Format the Story 038 commercial report template with safe aggregates only."""
    safe_metrics = {key: _safe_value(value) for key, value in metrics.items()}
    objections = _safe_list(safe_metrics.get("top_objections"), limit=3)
    actions = safe_metrics.get("actions") if isinstance(safe_metrics.get("actions"), dict) else {}
    return (
        "Resumo diario - Livia / Raiz Vital\n\n"
        "Atendimentos:\n"
        f"- Leads atendidos: {_count(safe_metrics.get('leads_attended'))}\n"
        f"- Leads quentes: {_count(safe_metrics.get('hot_leads'))}\n"
        f"- Checkouts pedidos: {_count(safe_metrics.get('checkouts_requested'))}\n"
        f"- Links gerados: {_count(safe_metrics.get('links_generated'))}\n"
        f"- Pagamentos confirmados: {_count(safe_metrics.get('payments_confirmed'))}\n"
        f"- Potes vendidos: {_count(safe_metrics.get('pots_sold'))}\n"
        f"- Receita estimada: {_currency_brl(safe_metrics.get('estimated_revenue_cents'))}\n\n"
        "Conversao:\n"
        f"- Oferta -> checkout: {_percent(safe_metrics.get('offer_to_checkout_rate'))}\n"
        f"- Checkout -> pagamento: {_percent(safe_metrics.get('checkout_to_payment_rate'))}\n"
        f"- Potes por pedido: {_decimal(safe_metrics.get('pots_per_paid_order'))}\n\n"
        "Follow-ups:\n"
        f"- Feitos: {_count(safe_metrics.get('followups_sent'))}\n"
        f"- Responderam: {_count(safe_metrics.get('followups_replied'))}\n"
        f"- Viraram checkout: {_count(safe_metrics.get('followups_to_checkout'))}\n"
        f"- Viraram venda: {_count(safe_metrics.get('followups_to_payment'))}\n\n"
        "Top objecoes:\n"
        f"1. {objections[0]}\n"
        f"2. {objections[1]}\n"
        f"3. {objections[2]}\n\n"
        "Acoes para Fernando:\n"
        f"- Depoimento para pedir: {_safe_action_text(actions.get('testimonial_to_request'))}\n"
        f"- Lead que precisa humano: {_safe_action_text(actions.get('human_needed'))}\n"
        f"- Problema operacional: {_safe_action_text(actions.get('operational_problem'))}"
    )


async def send_commercial_daily_report(metrics: dict[str, Any], whatsapp_tool: Any) -> bool:
    return await notify_fernando(
        text=format_commercial_daily_report(metrics),
        whatsapp_tool=whatsapp_tool,
        session_id="livia_commercial_daily_report",
    )


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _safe_value(item) for key, item in value.items() if not contains_sensitive_value(item)}
    if isinstance(value, list):
        return [_safe_value(item) for item in value if not contains_sensitive_value(item)]
    if contains_sensitive_value(value):
        return "[redacted]"
    return value


def _safe_list(value: Any, limit: int) -> list[str]:
    values = value if isinstance(value, list) else []
    result = [_text(item) for item in values[:limit]]
    return result + [UNAVAILABLE] * (limit - len(result))


def _text(value: Any) -> str:
    if value in (None, ""):
        return UNAVAILABLE
    return str(value)


def _safe_action_text(value: Any) -> str:
    text = _text(value)
    if text == UNAVAILABLE:
        return text
    if _looks_like_freeform_pii(text):
        return "[redacted]"
    return text


def _looks_like_freeform_pii(text: str) -> bool:
    normalized = text.lower()
    address_terms = (
        "rua",
        "avenida",
        "av.",
        "travessa",
        "alameda",
        "estrada",
        "rodovia",
        "bairro",
        "cep",
        "numero",
        "número",
        "apto",
        "apartamento",
    )
    if contains_sensitive_value(text):
        return True
    if any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in address_terms):
        return True
    if re.search(r"\b[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+ [A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+\b", text):
        return True
    return False


def _count(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    return str(int(value))


def _decimal(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{float(value):.2f}".replace(".", ",")


def _percent(value: Any) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{float(value) * 100:.1f}%".replace(".", ",")


def _currency_brl(cents: Any) -> str:
    if cents is None:
        return UNAVAILABLE
    return f"R$ {int(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
