"""VendedorAgent — primeiro contato, apresentacao, qualificacao, link de compra."""
from __future__ import annotations

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.conversion.payment_gate import make_guarded_payment_link_generator
from zwaf.tools.catalog import make_catalog_search
from zwaf.tools.whatsapp import WhatsAppTool


def build_vendedor_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
    payment_result_sink: dict | None = None,
    lead_memory_block: str = "",
) -> Agent:
    """
    Vendedor: primeiro contato, apresentacao do produto, contorno de objecoes,
    envio de link de pagamento. Nao da desconto sem aprovacao explicita no config.

    payment_result_sink: dict mutavel (por request) onde a tool de pagamento
    registra mensagens deterministicas de checkout (ex.: CPF invalido) para que o
    coordenador as envie literalmente, sem parafrase do LLM.
    """
    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
        make_catalog_search(tenant_config.tenant_id),
        make_guarded_payment_link_generator(
            tenant_config.tenant_id,
            tenant_config.payment,
            result_sink=payment_result_sink,
            session_id=session_id,
            lead_id=lead_id,
        ),
    ]

    return build_agent(
        agent_name="vendedor",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
        lead_memory_block=lead_memory_block,
    )
