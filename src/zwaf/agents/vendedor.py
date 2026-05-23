"""VendedorAgent — primeiro contato, apresentação, qualificação, link de compra."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool


def build_vendedor_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Vendedor: primeiro contato, apresentação do produto, contorno de objeções,
    envio de link de pagamento. Não dá desconto sem aprovação explícita no config.
    """
    from zwaf.tools.catalog import search_catalog
    from zwaf.tools.payment import generate_payment_link

    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        search_catalog,
        generate_payment_link,
    ]

    return build_agent(
        agent_name="vendedor",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
    )
