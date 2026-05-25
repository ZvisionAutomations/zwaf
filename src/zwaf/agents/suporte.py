"""SuporteAgent — duvidas, problemas, consulta de status de pedido."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.catalog import make_catalog_search
from zwaf.tools.escalation import escalate_to_human
from zwaf.tools.whatsapp import WhatsAppTool


def build_suporte_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Suporte: responde duvidas com base na knowledge base (RAG),
    consulta status de pedido, escala para humano em casos de:
    - reembolso, reacao adversa, defeito fisico (direto para Fernando)
    - solicitacao explicita de humano (tenta resolver 1-2 turnos antes de escalar)
    """
    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        make_catalog_search(tenant_config.tenant_id),
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