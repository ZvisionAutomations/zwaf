"""Payment-link guardrails for tenant checkout tools."""
from __future__ import annotations

from typing import Any, Callable, Optional

from zwaf.conversion.checkout_policy import validate_checkout_ready
from zwaf.conversion.intelligence import ConversionAction, decide_payment_link
from zwaf.tools.payment import make_payment_link_generator


def make_guarded_payment_link_generator(
    tenant_id: str,
    payment_config: Optional[dict[str, Any]] = None,
) -> Callable:
    """Return a payment tool that requires explicit buying-intent evidence."""
    raw_generator = make_payment_link_generator(tenant_id, payment_config)

    async def generate_payment_link(
        product_id: str,
        customer_phone: str,
        customer_name: str = "",
        customer_document: str = "",
        delivery_address: Optional[dict[str, Any]] = None,
        buying_intent_evidence: str = "",
        billing_type: str = "",
    ) -> str:
        checkout = validate_checkout_ready(
            tenant_id=tenant_id,
            product_id=product_id,
            customer_name=customer_name,
            customer_document=customer_document,
            delivery_address=delivery_address,
        )
        if not checkout.ok:
            if checkout.code == "blocked_product":
                return "Nao vou gerar esse link por aqui. Alpha Pulse precisa ser atendido pelo Caio."
            missing = ", ".join(checkout.missing_fields)
            return (
                "Antes de te mandar o link, preciso completar o pedido com "
                f"estes dados: {missing}."
            )

        decision = decide_payment_link(
            product_id=product_id,
            buying_intent_evidence=buying_intent_evidence,
            tenant_id=tenant_id,
        )

        if decision.should_send_payment_link:
            return await raw_generator(
                product_id=product_id,
                customer_phone=customer_phone,
                customer_name=customer_name,
                customer_document=customer_document,
                delivery_address=delivery_address,
                billing_type=billing_type,
            )

        if decision.action == ConversionAction.TRANSFER_AGENT:
            return "Nao vou gerar esse link por aqui. Esse produto precisa ser atendido pelo consultor correto."

        if decision.action == ConversionAction.ESCALATE_HUMAN:
            return "Nao vou enviar link agora. Vou chamar uma pessoa da equipe para te ajudar com seguranca."

        if decision.action == ConversionAction.HANDLE_OBJECTION:
            return "Antes do link, deixa eu te ajudar a avaliar o custo-beneficio e tirar sua duvida."

        return "Antes de te mandar o link, confirma pra mim: voce quer fechar o pedido agora?"

    generate_payment_link.__name__ = "generate_payment_link"
    return generate_payment_link
