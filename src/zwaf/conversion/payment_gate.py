"""Payment-link guardrails for tenant checkout tools."""
from __future__ import annotations

from typing import Any, Callable, Optional

from zwaf.conversion.address_attempts import record_address_failure, reset_attempts
from zwaf.conversion.address_resolver import resolve_delivery_address
from zwaf.conversion.checkout_policy import validate_checkout_ready
from zwaf.conversion.intelligence import ConversionAction, decide_payment_link
from zwaf.tools.payment import make_payment_link_generator


def _is_address_only_failure(missing_fields: list[str]) -> bool:
    """True quando todos os campos faltantes sao de endereco (story-040 FR-7).

    O contador anti-loop so deve avancar quando a falha e puramente de endereco;
    CPF/nome faltante seguem o fluxo normal da story-035 sem escalar por endereco.
    """
    if not missing_fields:
        return False
    return all(field.startswith("delivery_address.") for field in missing_fields)


def make_guarded_payment_link_generator(
    tenant_id: str,
    payment_config: Optional[dict[str, Any]] = None,
    result_sink: Optional[dict[str, Any]] = None,
    session_id: str = "",
    lead_id: str = "",
) -> Callable:
    """Return a payment tool that requires explicit buying-intent evidence.

    result_sink: dict mutavel (por request) onde a tool registra respostas
    DETERMINISTICAS (qualquer retorno que NAO seja uma URL de pagamento). O
    coordenador (ZWAFTeam) usa isso para enviar a mensagem literal ao cliente,
    sem deixar o LLM parafrasear erros de checkout (ex.: "CPF invalido"). Em caso
    de sucesso (URL http), nada e registrado e o LLM compoe a resposta natural.
    """
    raw_generator = make_payment_link_generator(tenant_id, payment_config)

    def _record(message: str) -> str:
        """Registra mensagem deterministica no sink quando NAO for uma URL."""
        if result_sink is not None and not str(message).startswith("http"):
            result_sink["deterministic_reply"] = message
        return message

    async def generate_payment_link(
        product_id: str,
        customer_phone: str,
        customer_name: str = "",
        customer_document: str = "",
        delivery_address: Optional[dict[str, Any]] = None,
        buying_intent_evidence: str = "",
        billing_type: str = "",
        quantity: int = 0,
    ) -> str:
        # Story-040: resolve o endereco (str|dict) via parser + ViaCEP ANTES de
        # validar. ViaCEP completa street/district/city/state a partir do CEP;
        # fallback resiliente usa os campos do LLM se o ViaCEP falhar (FR-6/NFR-2).
        resolved_address = await resolve_delivery_address(delivery_address)

        checkout = validate_checkout_ready(
            tenant_id=tenant_id,
            product_id=product_id,
            customer_name=customer_name,
            customer_document=customer_document,
            delivery_address=resolved_address,
        )
        if not checkout.ok:
            if checkout.code == "blocked_product":
                return _record(checkout.message or (
                    "Nao vou gerar esse link por aqui. Esse produto e atendido por "
                    "outro consultor da Raiz Vital."
                ))
            # Story-040 FR-7: so o caminho de endereco alimenta o anti-loop. CPF/
            # nome faltante (035) NAO escalam por endereco.
            if _is_address_only_failure(checkout.missing_fields):
                attempts = record_address_failure(session_id, lead_id)
                if result_sink is not None:
                    result_sink["address_attempts"] = attempts
            missing = _format_missing_checkout_fields(checkout.missing_fields)
            return _record(
                "Antes de te mandar o link, preciso completar o pedido com "
                f"estes dados: {missing}."
            )

        # Endereco completo -> zera o contador anti-loop da sessao.
        reset_attempts(session_id, lead_id)

        decision = decide_payment_link(
            product_id=product_id,
            buying_intent_evidence=buying_intent_evidence,
            tenant_id=tenant_id,
        )

        if decision.should_send_payment_link:
            return _record(await raw_generator(
                product_id=product_id,
                customer_phone=customer_phone,
                customer_name=customer_name,
                customer_document=customer_document,
                delivery_address=resolved_address,
                billing_type=billing_type,
                quantity=quantity,
            ))

        if decision.action == ConversionAction.TRANSFER_AGENT:
            return _record("Nao vou gerar esse link por aqui. Esse produto precisa ser atendido pelo consultor correto.")

        if decision.action == ConversionAction.ESCALATE_HUMAN:
            return _record("Nao vou enviar link agora. Vou chamar uma pessoa da equipe para te ajudar com seguranca.")

        if decision.action == ConversionAction.HANDLE_OBJECTION:
            return _record("Antes do link, deixa eu te ajudar a avaliar o custo-beneficio e tirar sua duvida.")

        if decision.action in {ConversionAction.ASK_FOLLOWUP, ConversionAction.ANSWER_QUESTION}:
            return _record(await raw_generator(
                product_id=product_id,
                customer_phone=customer_phone,
                customer_name=customer_name,
                customer_document=customer_document,
                delivery_address=resolved_address,
                billing_type=billing_type,
                quantity=quantity,
            ))

        return _record("Nao consegui confirmar o fechamento do pedido neste momento.")

    generate_payment_link.__name__ = "generate_payment_link"
    return generate_payment_link


def _format_missing_checkout_fields(missing_fields: list[str]) -> str:
    labels = {
        "customer_name": "nome completo",
        "customer_document": "CPF/CNPJ valido",
        "customer_document_invalid": "um CPF valido (o numero informado nao e um CPF valido, confira os digitos)",
        "delivery_address.postal_code": "CEP",
        "delivery_address.street": "rua",
        "delivery_address.number": "numero",
        "delivery_address.district": "bairro",
        "delivery_address.city": "cidade",
        "delivery_address.state": "UF",
    }
    parts = [labels.get(field, field) for field in missing_fields]
    if not parts:
        return "os dados minimos do pedido"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} e {parts[1]}"
    return ", ".join(parts[:-1]) + f" e {parts[-1]}"
