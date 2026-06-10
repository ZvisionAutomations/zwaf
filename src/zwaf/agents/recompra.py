"""RecompraAgent — cliente ativo que quer pedir novamente."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.catalog import make_catalog_search
from zwaf.tools.payment import make_payment_link_generator
from zwaf.tools.whatsapp import WhatsAppTool


def build_recompra_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
    lead_memory_block: str = "",
) -> Agent:
    """
    Recompra: reconhece cliente ativo, gera link de recompra rapido,
    aplica desconto de fidelidade se configurado, registra novo pedido.
    Meta: link de pagamento enviado em <=3 turnos.
    """
    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        make_catalog_search(tenant_config.tenant_id),
        make_payment_link_generator(tenant_config.tenant_id, tenant_config.payment),
    ]

    return build_agent(
        agent_name="recompra",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
        lead_memory_block=lead_memory_block,
    )