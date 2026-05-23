"""CobrancaAgent — pagamento pendente, geração de novo link."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool


def build_cobranca_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Cobrança: identifica problema de pagamento, gera novo link Pix/boleto,
    instrui cliente sobre como efetuar o pagamento, verifica status do pagamento.
    """
    from zwaf.tools.payment import generate_payment_link, check_payment_status

    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        generate_payment_link,
        check_payment_status,
    ]

    return build_agent(
        agent_name="cobranca",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
    )
