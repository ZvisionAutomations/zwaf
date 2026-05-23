"""
ZWAFTeam — Coordenador multi-agente do ZWAF.

Orquestra o RouterAgent + 5 agentes especializados.
Usa Agno Team(mode="route") quando disponível, com fallback para
coordenação manual via RouterAgent.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from zwaf.core.router_agent import RouterAgent, RouteResult
from zwaf.core.tenant import TenantConfig
from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.core.team")


@dataclass
class TeamResponse:
    response: str
    agent_used: str
    session_id: str
    lead_id: str
    latency_ms: float
    route_result: RouteResult


class ZWAFTeam:
    """
    Coordenador multi-agente.

    Fluxo:
    1. InputGuard sanitiza/bloqueia mensagem
    2. RouterAgent classifica e roteia
    3. Agente especializado processa
    4. Resposta retorna ao webhook

    Os agentes Agno são construídos on-demand por sessão (stateless factory pattern).
    """

    def __init__(
        self,
        tenant_config: TenantConfig,
        whatsapp_tool: WhatsAppTool,
        router: RouterAgent,
        db_url: str = "",
    ):
        self._tenant = tenant_config
        self._whatsapp = whatsapp_tool
        self._router = router
        self._db_url = db_url
        self._guard = _build_guard()

    async def send_response(self, phone: str, text: str, session_id: str) -> None:
        """Envia resposta via WhatsApp — interface pública para o webhook."""
        await self._whatsapp.send_message(phone=phone, text=text, session_id=session_id)

    async def process(
        self,
        message: str,
        phone: str,
        session_id: str,
        lead_id: str,
    ) -> TeamResponse:
        """Processa uma mensagem do lead e retorna a resposta."""
        start = time.monotonic()

        # 1. Security guard
        guard_result = self._guard.check(text=message, session_id=session_id, lead_id=lead_id)
        if guard_result.should_block:
            logger.warning(
                "Security incident blocked",
                extra={"session_id": session_id, "lead_id": lead_id},
            )
            return TeamResponse(
                response=guard_result.deflection_message,
                agent_used="guard",
                session_id=session_id,
                lead_id=lead_id,
                latency_ms=(time.monotonic() - start) * 1000,
                route_result=RouteResult("guard", 1.0),
            )

        # 2. Route
        route = await self._router.route(guard_result.sanitized_input, phone=phone)
        logger.info(
            "Routed message",
            extra={
                "agent": route.agent_name,
                "confidence": route.confidence,
                "via_llm": route.via_llm,
                "session_id": session_id,
            },
        )

        # 3. Execute agent
        response_text = await self._run_agent(
            agent_name=route.agent_name,
            message=guard_result.sanitized_input,
            session_id=session_id,
            lead_id=lead_id,
        )

        return TeamResponse(
            response=response_text,
            agent_used=route.agent_name,
            session_id=session_id,
            lead_id=lead_id,
            latency_ms=(time.monotonic() - start) * 1000,
            route_result=route,
        )

    async def _run_agent(
        self,
        agent_name: str,
        message: str,
        session_id: str,
        lead_id: str,
    ) -> str:
        """Constrói e executa o agente especializado com fallback."""
        agent = self._build_agent(agent_name, session_id, lead_id)
        try:
            run_response = await agent.arun(message)
            return run_response.content or ""
        except Exception as e:
            logger.error(
                "Agent execution failed",
                extra={"agent": agent_name, "session_id": session_id, "error": str(e)},
            )
            # Fallback gracioso
            return (
                "Desculpe, estou com uma dificuldade técnica no momento. "
                "Pode me enviar sua mensagem novamente em instantes?"
            )

    def _build_agent(self, agent_name: str, session_id: str, lead_id: str):
        """Factory: constrói o agente Agno correto para o nome dado."""
        kwargs = dict(
            tenant_config=self._tenant,
            whatsapp_tool=self._whatsapp,
            session_id=session_id,
            lead_id=lead_id,
            db_url=self._db_url,
        )

        if agent_name == "vendedor":
            from zwaf.agents.vendedor import build_vendedor_agent
            return build_vendedor_agent(**kwargs)
        if agent_name == "recompra":
            from zwaf.agents.recompra import build_recompra_agent
            return build_recompra_agent(**kwargs)
        if agent_name == "suporte":
            from zwaf.agents.suporte import build_suporte_agent
            return build_suporte_agent(**kwargs)
        if agent_name == "cobranca":
            from zwaf.agents.cobranca import build_cobranca_agent
            return build_cobranca_agent(**kwargs)

        # Default: vendedor
        logger.warning("Unknown agent name '%s' — defaulting to vendedor", agent_name)
        from zwaf.agents.vendedor import build_vendedor_agent
        return build_vendedor_agent(**kwargs)


def _build_guard():
    """Constrói InputGuard para o ZWAF (fork do guard da Sofia SDR)."""
    from zwaf.security.guard import InputGuard
    return InputGuard()


def build_team(
    tenant_config: TenantConfig,
    db_url: str = "",
) -> ZWAFTeam:
    """
    Factory principal: constrói o ZWAFTeam completo para um tenant.
    Inicializa WhatsAppTool, RouterAgent e os 5 agentes lazy.
    """
    # WhatsApp tool com config do tenant
    if tenant_config.whatsapp.phone_numbers:
        whatsapp = WhatsAppTool.from_phone_entries(
            entries=tenant_config.whatsapp.phone_numbers,
            api_key=tenant_config.whatsapp.evolution_api_key,
            base_url=tenant_config.whatsapp.evolution_api_url,
            messages_per_minute=tenant_config.whatsapp.messages_per_minute,
            typing_simulation=tenant_config.whatsapp.typing_simulation,
            warm_up_mode=tenant_config.whatsapp.warm_up_mode,
            warm_up_day=tenant_config.whatsapp.current_warm_up_day,
        )
    else:
        whatsapp = WhatsAppTool(
            api_key=tenant_config.whatsapp.evolution_api_key,
            base_url=tenant_config.whatsapp.evolution_api_url,
            messages_per_minute=tenant_config.whatsapp.messages_per_minute,
        )

    # Router com purchase history lookup via Redis (injetado via closure)
    router = RouterAgent(
        config=tenant_config.router,
        # purchase_history_fn será injetada pelo lifespan quando Redis estiver disponível
    )

    team = ZWAFTeam(
        tenant_config=tenant_config,
        whatsapp_tool=whatsapp,
        router=router,
        db_url=db_url,
    )
    return team
