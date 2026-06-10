"""BaseWhatsAppAgent - base para todos os agentes especializados do ZWAF."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from agno.agent import Agent
from agno.models.openai import OpenAIChat

try:
    from agno.db.postgres import AsyncPostgresDb
except ImportError:
    AsyncPostgresDb = None  # type: ignore

from zwaf.core.tenant import TenantConfig

logger = logging.getLogger("zwaf.agents.base")

_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"


def _load_prompt(tenant_id: str, agent_name: str) -> str:
    """Carrega o system prompt de tenants/{tenant_id}/prompts/{agent_name}.md.

    Se existir um KB do agente (`{agent_name}.kb.md` no mesmo diretorio), ele e
    ANEXADO ao prompt — assim o conteudo de apoio (ex.: persuasao da Livia) entra
    de fato no contexto do modelo, em vez de ser so uma referencia que ele nao le.
    """
    prompt_dir = _TENANTS_ROOT / tenant_id / "prompts"
    prompt_path = prompt_dir / f"{agent_name}.md"
    if not prompt_path.exists():
        logger.warning("Prompt file not found: %s - using default", prompt_path)
        return f"Voce e a {agent_name} da empresa. Ajude o cliente de forma cordial e eficiente."

    prompt = prompt_path.read_text(encoding="utf-8")
    kb_path = prompt_dir / f"{agent_name}.kb.md"
    if kb_path.exists():
        kb = kb_path.read_text(encoding="utf-8").strip()
        if kb:
            prompt = f"{prompt}\n\n---\n\n{kb}"
            logger.info("KB anexado ao prompt: %s", kb_path.name)
    return prompt


def _make_llm(tenant_config: TenantConfig):
    """Cria o modelo LLM conforme config do tenant (primary model)."""
    model_id = tenant_config.llm.primary
    temperature = tenant_config.llm.temperature

    if "gpt" in model_id or "openai" in model_id:
        return OpenAIChat(id=model_id, temperature=temperature)

    if "gemini" in model_id:
        return OpenAIChat(
            id=f"google/{model_id}" if "/" not in model_id else model_id,
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
        )

    return OpenAIChat(id=model_id, temperature=temperature)


def _make_db(db_url: str, tenant_id: str, agent_name: str):
    if not db_url or AsyncPostgresDb is None:
        return None

    agno_db_url = db_url.replace("+asyncpg", "+psycopg_async")
    session_table = f"zwaf_{tenant_id.replace('-', '_')}_{agent_name}_sessions"
    try:
        return AsyncPostgresDb(
            db_url=agno_db_url,
            session_table=session_table,
        )
    except Exception as e:
        logger.warning("Agno DB disabled for %s: %s", agent_name, e)
        return None


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
    db = _make_db(db_url, tenant_config.tenant_id, agent_name)

    agent_kwargs = {
        "name": f"{tenant_config.agent_name}_{agent_name}",
        "model": model,
        "instructions": system_prompt,
        "tools": tools,
        "session_id": session_id,
        "user_id": lead_id,
        "add_history_to_context": True,
        "num_history_runs": 10,
        "reasoning": False,
        "markdown": False,
    }
    if db is not None:
        agent_kwargs["db"] = db

    return Agent(**agent_kwargs)
