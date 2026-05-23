"""
FidelizacaoAgent — acionado por evento agendado (30 dias pós-compra).

IMPORTANTE: Este agente NUNCA é roteado por mensagem do lead.
É acionado exclusivamente pelo APScheduler via FidelizacaoScheduler.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from agno.agent import Agent

from zwaf.core.base_agent import build_agent
from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.agents.fidelizacao")


@dataclass
class FidelizacaoEvent:
    """Evento de fidelização para um lead específico."""
    lead_id: str
    phone: str
    purchase_date: datetime
    product_id: str
    tenant_id: str


def build_fidelizacao_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Fidelização: envia mensagem de acompanhamento 30 dias pós-compra,
    coleta NPS se habilitado, oferece recompra com incentivo.
    """
    tools = [
        whatsapp_tool.send_message,
        whatsapp_tool._set_typing,
    ]

    return build_agent(
        agent_name="fidelizacao",
        tenant_config=tenant_config,
        tools=tools,
        session_id=session_id,
        lead_id=lead_id,
        db_url=db_url,
    )


class FidelizacaoScheduler:
    """
    Integração com APScheduler para disparar FidelizacaoAgent
    automaticamente N dias após a compra.

    Registrado no lifespan FastAPI da aplicação.
    """

    def __init__(self, tenant_config: TenantConfig, whatsapp_tool: WhatsAppTool, db_url: str = ""):
        self._tenant_config = tenant_config
        self._whatsapp_tool = whatsapp_tool
        self._db_url = db_url
        self._scheduler = None

    def start(self) -> None:
        """Inicia APScheduler com job diário de verificação."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        if self._tenant_config.fidelizacao is None:
            logger.info("Fidelizacao disabled for tenant %s", self._tenant_config.tenant_id)
            return

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._check_and_dispatch,
            trigger="cron",
            hour=9,
            minute=0,
            id=f"fidelizacao_{self._tenant_config.tenant_id}",
        )
        self._scheduler.start()
        logger.info("FidelizacaoScheduler started for tenant %s", self._tenant_config.tenant_id)

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _check_and_dispatch(self) -> None:
        """
        Verifica leads com compra N dias atrás e dispara o agente de fidelização.
        N = fidelizacao.trigger_days_after_purchase do config.
        """
        if self._tenant_config.fidelizacao is None:
            return

        trigger_days = self._tenant_config.fidelizacao.get("trigger_days_after_purchase", 30)
        logger.info(
            "Checking fidelizacao events",
            extra={"tenant": self._tenant_config.tenant_id, "trigger_days": trigger_days},
        )

        # TODO Fase 2: consultar PostgreSQL por leads com compra há trigger_days dias
        # Por ora, loga e retorna (MVP sem DB wiring)
        logger.debug("Fidelizacao check: DB query not yet wired (Phase 2)")
