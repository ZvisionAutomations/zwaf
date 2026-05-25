"""
FidelizacaoAgent — acionado por evento agendado (N dias pos-compra).

IMPORTANTE: Este agente NUNCA e roteado por mensagem do lead.
E acionado exclusivamente pelo APScheduler via FidelizacaoScheduler.
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
    """Evento de fidelizacao para um lead especifico."""
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
    Fidelizacao: envia mensagem de acompanhamento N dias pos-compra,
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
    Integra com APScheduler para disparar FidelizacaoAgent
    automaticamente N dias apos a compra (cron diario as 9h).

    Registrado via build_team no lifespan FastAPI.
    """

    def __init__(
        self,
        tenant_config: TenantConfig,
        whatsapp_tool: WhatsAppTool,
        db_url: str = "",
    ):
        self._tenant_config = tenant_config
        self._whatsapp_tool = whatsapp_tool
        self._db_url = db_url
        self._scheduler = None

    def start(self) -> None:
        """Inicia APScheduler com job diario de verificacao."""
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
        logger.info(
            "FidelizacaoScheduler started for tenant %s",
            self._tenant_config.tenant_id,
        )

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _check_and_dispatch(self) -> None:
        """
        Verifica payment_events com compra PAID ha exatamente trigger_days dias.
        Para cada lead encontrado, dispara o FidelizacaoAgent uma vez.

        Janela: dia exato (CURRENT_DATE - trigger_days). Cron diario garante cobertura.
        """
        if self._tenant_config.fidelizacao is None:
            return

        trigger_days = self._tenant_config.fidelizacao.get("trigger_days_after_purchase", 30)
        tenant_id = self._tenant_config.tenant_id

        logger.info(
            "Fidelizacao check running",
            extra={"tenant": tenant_id, "trigger_days": trigger_days},
        )

        if not self._db_url:
            logger.warning("DATABASE_URL not set — fidelizacao skipped")
            return

        clean_url = self._db_url.replace("+asyncpg", "")
        try:
            import asyncpg
            conn = await asyncpg.connect(clean_url)
            try:
                # Busca leads com primeira compra PAID ha trigger_days dias
                # Usa MIN(created_at) para nao reenviar em recompras
                rows = await conn.fetch(
                    """
                    SELECT
                        lead_phone,
                        product_id,
                        MIN(created_at) AS first_purchase_date
                    FROM payment_events
                    WHERE
                        tenant_id = $1
                        AND status = 'PAID'
                    GROUP BY lead_phone, product_id
                    HAVING MIN(created_at)::date = (CURRENT_DATE - $2::int)
                    """,
                    tenant_id,
                    trigger_days,
                )
            finally:
                await conn.close()
        except Exception as e:
            logger.error("Fidelizacao DB query failed: %s", e)
            return

        if not rows:
            logger.info(
                "No fidelizacao events today",
                extra={"tenant": tenant_id, "trigger_days": trigger_days},
            )
            return

        logger.info(
            "Dispatching fidelizacao for %d leads",
            len(rows),
            extra={"tenant": tenant_id},
        )

        for row in rows:
            phone = row["lead_phone"]
            product_id = row["product_id"]
            session_id = f"fidelizacao_{tenant_id}_{phone}_{trigger_days}d"

            try:
                agent = build_fidelizacao_agent(
                    tenant_config=self._tenant_config,
                    whatsapp_tool=self._whatsapp_tool,
                    session_id=session_id,
                    lead_id=phone,
                    db_url=self._db_url,
                )
                # Mensagem interna aciona o prompt de fidelizacao
                trigger_message = (
                    f"[FIDELIZACAO] Lead completou {trigger_days} dias desde a compra de {product_id}. "
                    f"Iniciar fluxo de fidelizacao conforme prompt."
                )
                await agent.arun(trigger_message)
                logger.info(
                    "Fidelizacao dispatched",
                    extra={"phone_tail": phone[-4:], "product_id": product_id},
                )
            except Exception as e:
                logger.error(
                    "Fidelizacao dispatch failed for lead",
                    extra={"phone_tail": phone[-4:], "error": str(e)},
                )