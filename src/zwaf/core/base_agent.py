"""BaseWhatsAppAgent — base para todos os agentes especializados do ZWAF."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from agno.agent import Agent
from agno.models.openai import OpenAIChat

try:
    from agno.storage.agent.postgres import PostgresAgentStorage
except ImportError:
    try:
        from agno.storage.postgres import PostgresAgentStorage
    except ImportError:
        PostgresAgentStorage = None  # type: ignore

from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.agents.base")

_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"


def _load_prompt(tenant_id: str, agent_name: str) -> str:
    """Carrega system prompt de tenants/{tenant_id}/prompts/{agent_name}.md."""
    prompt_path = _TENANTS_ROOT / tenant_id / "prompts" / f"{agent_name}.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    logger.warning("Prompt file not found: %s — using default", prompt_path)
    return f"Você é a {agent_name} da empresa. Ajude o cliente de forma cordial e eficiente."


def _make_llm(tenant_config: TenantConfig):
    """Cria o modelo LLM conforme config do tenant (primary model)."""
    model_id = tenant_config.llm.primary
    temperature = tenant_config.llm.temperature

    # OpenAI GPT (default) — outros providers expandíveis aqui
    if "gpt" in model_id or "openai" in model_id:
        return OpenAIChat(id=model_id, temperature=temperature)

    # Gemini via OpenRouter
    if "gemini" in model_id:
        return OpenAIChat(
            id=f"google/{model_id}" if "/" not in model_id else model_id,
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
        )

    # Fallback: tenta como OpenAI-compatible
    return OpenAIChat(id=model_id, temperature=temperature)


def build_agent(
    agent_name: str,
    tenant_config: TenantConfig,
    tools: list,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Factory para construir um Agno Agent configurado para um tenant.
    Carrega o system prompt de tenants/{tenant_id}/prompts/{agent_name}.md.
    """
    system_prompt = _load_prompt(tenant_config.tenant_id, agent_name)
    model = _make_llm(tenant_config)

    storage_kwargs = {}
    if db_url and PostgresAgentStorage is not None:
        storage_kwargs["storage"] = PostgresAgentStorage(
            db_url=db_url,
            table_name=f"zwaf_{tenant_config.tenant_id}_{agent_name}_sessions",
        )

    return Agent(
        name=f"{tenant_config.agent_name}_{agent_name}",
        model=model,
        instructions=system_prompt,
        tools=tools,
        session_id=session_id,
        user_id=lead_id,
        add_history_to_messages=True,
        num_history_responses=10,
        reasoning=False,  # Desabilitado para reduzir latência em B2C
        markdown=False,
        show_tool_calls=False,
        **storage_kwargs,
    )
