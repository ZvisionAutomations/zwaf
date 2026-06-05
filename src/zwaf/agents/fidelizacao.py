"""
FidelizacaoAgent — acionado por evento agendado (N dias pos-compra).

IMPORTANTE: Este agente NUNCA e roteado por mensagem do lead.
E acionado exclusivamente pelo APScheduler via FidelizacaoScheduler.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
    delivered_at: datetime
    product_id: str
    tenant_id: str
    kind: str = "delivery_30d_coupon"


def build_fidelizacao_agent(
    tenant_config: TenantConfig,
    whatsapp_tool: WhatsAppTool,
    session_id: str,
    lead_id: str,
    db_url: str = "",
) -> Agent:
    """
    Fidelizacao: envia mensagem de acompanhamento N dias pos-entrega,
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
    automaticamente apos eventos de entrega (cron diario as 9h).

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
        self._scheduler: Any | None = None

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
        Verifica followup_events agendados a partir de entrega/recebimento.
        Para cada lead encontrado, dispara o FidelizacaoAgent uma vez.
        """
        if self._tenant_config.fidelizacao is None:
            return

        tenant_id = self._tenant_config.tenant_id

        logger.info(
            "Fidelizacao check running",
            extra={"tenant": tenant_id},
        )

        if not self._db_url:
            logger.warning("DATABASE_URL not set — fidelizacao skipped")
            return

        clean_url = self._db_url.replace("+asyncpg", "")
        try:
            import asyncpg
            conn = await asyncpg.connect(clean_url)
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        o.lead_phone,
                        o.product_id,
                        f.kind,
                        s.delivered_at
                    FROM followup_events f
                    JOIN orders o ON o.id = f.order_id
                    LEFT JOIN shipments s ON s.order_id = o.id
                    WHERE
                        o.tenant_id = $1
                        AND f.status = 'scheduled'
                        AND f.scheduled_for <= NOW()
                        AND NOT EXISTS (
                            SELECT 1
                            FROM lead_profiles lp
                            WHERE lp.tenant_id = o.tenant_id
                              AND lp.phone = o.lead_phone
                              AND lp.opt_out_at IS NOT NULL
                        )
                    ORDER BY f.scheduled_for ASC
                    """,
                    tenant_id,
                )
            finally:
                await conn.close()
        except Exception as e:
            logger.error("Fidelizacao DB query failed: %s", e)
            return

        if not rows:
            logger.info(
                "No fidelizacao events today",
                extra={"tenant": tenant_id},
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
            kind = row["kind"]
            session_id = f"fidelizacao_{tenant_id}_{phone}_{kind}"

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
                    f"[FIDELIZACAO] Evento {kind} para {product_id}. "
                    f"Iniciar fluxo de fidelizacao conforme entrega/recebimento."
                )
                await agent.arun(trigger_message)
                await self._mark_followup_sent(phone=phone, product_id=product_id, kind=kind)
                logger.info(
                    "Fidelizacao dispatched",
                    extra={"phone_tail": phone[-4:], "product_id": product_id, "kind": kind},
                )
            except Exception as e:
                logger.error(
                    "Fidelizacao dispatch failed for lead",
                    extra={"phone_tail": phone[-4:], "error": str(e)},
                )

    async def _mark_followup_sent(self, phone: str, product_id: str, kind: str) -> None:
        if not self._db_url:
            return
        clean_url = self._db_url.replace("+asyncpg", "")
        try:
            import asyncpg
            conn = await asyncpg.connect(clean_url)
            try:
                await conn.execute(
                    """
                    UPDATE followup_events f
                    SET status = 'sent', sent_at = NOW(), updated_at = NOW()
                    FROM orders o
                    WHERE f.order_id = o.id
                      AND o.tenant_id = $1
                      AND o.lead_phone = $2
                      AND o.product_id = $3
                      AND f.kind = $4
                      AND f.status = 'scheduled'
                    """,
                    self._tenant_config.tenant_id,
                    phone,
                    product_id,
                    kind,
                )
            finally:
                await conn.close()
        except Exception as e:
            logger.warning("Fidelizacao followup update failed: %s", e)
