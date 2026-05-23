"""SuporteAgent — dúvidas, problemas, consulta de status de pedido."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool


def build_suporte_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Suporte: responde dúvidas com base na knowledge base (RAG),
    consulta status de pedido, escala para humano em casos de:
    - reembolso, reação adversa, defeito físico (direto para Fernando)
    - solicitação explícita de humano (tenta resolver 1-2 turnos antes de escalar)
    """
    from zwaf.tools.catalog import search_catalog
    from zwaf.tools.escalation import escalate_to_human

    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        search_catalog,
        escalate_to_human,
    ]

    return build_agent(
        agent_name="suporte",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
    )
