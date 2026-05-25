"""CobrancaAgent — pagamento pendente, geracao de novo link."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.payment import make_payment_link_generator, make_payment_status_checker
from zwaf.tools.whatsapp import WhatsAppTool


def build_cobranca_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Cobranca: identifica problema de pagamento, gera novo link Pix/boleto,
    instrui cliente sobre como efetuar o pagamento, verifica status do pagamento.
    """
    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        make_payment_link_generator(tenant_config.tenant_id, tenant_config.payment),
        make_payment_status_checker(),
    ]

    return build_agent(
        agent_name="cobranca",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
    )