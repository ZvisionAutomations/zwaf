"""
ZWAFTeam — Coordenador multi-agente do ZWAF.

Orquestra o RouterAgent + 5 agentes especializados.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from zwaf.conversion.checkout_policy import is_opt_out_message
from zwaf.conversion.intelligence import LeadSignal, analyze_message
from zwaf.core.router_agent import RouterAgent, RouteResult
from zwaf.core.tenant import TenantConfig
from zwaf.memory.lead_store import append_conversion_event, mark_opt_out
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
    conversion_signal: Optional[LeadSignal] = None


class ZWAFTeam:
    """
    Coordenador multi-agente.

    Fluxo:
    1. InputGuard sanitiza/bloqueia mensagem
    2. RouterAgent classifica e roteia
    3. Agente especializado processa
    4. Resposta retorna ao webhook
    """

    def __init__(
        self,
        tenant_config: TenantConfig,
        whatsapp_tool: WhatsAppTool,
        router: RouterAgent,
        db_url: str = "",
        fidelizacao_scheduler=None,
    ):
        self._tenant = tenant_config
        self._whatsapp = whatsapp_tool
        self._router = router
        self._db_url = db_url
        self._guard = _build_guard()
        # Referencia ao scheduler para shutdown no lifespan
        self._fidelizacao_scheduler = fidelizacao_scheduler

    async def send_response(self, phone: str, text: str, session_id: str) -> None:
        """Envia resposta via WhatsApp — interface publica para o webhook."""
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

        if is_opt_out_message(guard_result.sanitized_input):
            await mark_opt_out(
                phone=phone,
                tenant_id=self._tenant.tenant_id,
                reason="lead_requested_opt_out",
            )
            return TeamResponse(
                response=(
                    "Tudo bem, entendi. Vou encerrar o contato por aqui e marcar para "
                    "nao enviarmos novas mensagens."
                ),
                agent_used="opt_out",
                session_id=session_id,
                lead_id=lead_id,
                latency_ms=(time.monotonic() - start) * 1000,
                route_result=RouteResult("opt_out", 1.0),
            )

        # 2. Route
        conversion_signal = analyze_message(
            guard_result.sanitized_input,
            tenant_id=self._tenant.tenant_id,
        )
        route = await self._router.route(guard_result.sanitized_input, phone=phone)
        logger.info(
            "Routed message",
            extra={
                "agent": route.agent_name,
                "confidence": route.confidence,
                "via_llm": route.via_llm,
                "session_id": session_id,
                "conversion_action": conversion_signal.action.value,
                "buying_intent": conversion_signal.buying_intent.value,
            },
        )
        await append_conversion_event(
            phone=phone,
            tenant_id=self._tenant.tenant_id,
            session_id=session_id,
            lead_id=lead_id,
            agent_name=route.agent_name,
            signal=conversion_signal.to_dict(),
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
            conversion_signal=conversion_signal,
        )

    async def _run_agent(
        self,
        agent_name: str,
        message: str,
        session_id: str,
        lead_id: str,
    ) -> str:
        """Constroi e executa o agente especializado com fallback."""
        # Sink por-request: a tool de pagamento registra aqui mensagens
        # deterministicas de checkout (ex.: CPF invalido, dado faltante). Quando
        # presente, ela e enviada LITERALMENTE ao cliente, sem parafrase do LLM —
        # garante consistencia mesmo com modelos que tendem a reescrever erros.
        payment_sink: dict[str, Any] = {}
        agent = self._build_agent(agent_name, session_id, lead_id, result_sink=payment_sink)
        try:
            run_response = await agent.arun(message)
            llm_reply = run_response.content or ""
        except Exception as e:
            logger.error(
                "Agent execution failed",
                extra={"agent": agent_name, "session_id": session_id, "error": str(e)},
            )
            # Se a tool ja havia registrado uma mensagem deterministica antes da
            # excecao, prefira-a a uma resposta generica de erro.
            deterministic = payment_sink.get("deterministic_reply")
            if deterministic:
                return deterministic
            return (
                "Desculpe, estou com uma dificuldade tecnica no momento. "
                "Pode me enviar sua mensagem novamente em instantes?"
            )

        # Bypass deterministico: se a tool de checkout devolveu uma mensagem
        # (qualquer retorno que nao seja uma URL de pagamento), envie-a literal.
        deterministic = payment_sink.get("deterministic_reply")
        if deterministic:
            return deterministic
        return llm_reply

    def _build_agent(
        self,
        agent_name: str,
        session_id: str,
        lead_id: str,
        result_sink: Optional[dict] = None,
    ):
        """Factory: constroi o agente Agno correto para o nome dado."""
        kwargs: Any = dict(
            tenant_config=self._tenant,
            whatsapp_tool=self._whatsapp,
            session_id=session_id,
            lead_id=lead_id,
            db_url=self._db_url,
        )

        if agent_name == "vendedor":
            from zwaf.agents.vendedor import build_vendedor_agent
            return build_vendedor_agent(payment_result_sink=result_sink, **kwargs)
        if agent_name == "recompra":
            from zwaf.agents.recompra import build_recompra_agent
            return build_recompra_agent(**kwargs)
        if agent_name == "suporte":
            from zwaf.agents.suporte import build_suporte_agent
            return build_suporte_agent(**kwargs)
        if agent_name == "cobranca":
            from zwaf.agents.cobranca import build_cobranca_agent
            return build_cobranca_agent(**kwargs)

        if agent_name == "fidelizacao":
            logger.warning(
                "RouterAgent tentou rotear para 'fidelizacao' — agente nao e roteavel diretamente "
                "(FidelizacaoScheduler opera via cron, nao por mensagem). "
                "Redirecionando para vendedor."
            )
            from zwaf.agents.vendedor import build_vendedor_agent
            return build_vendedor_agent(payment_result_sink=result_sink, **kwargs)

        logger.warning("Unknown agent name '%s' — defaulting to vendedor", agent_name)
        from zwaf.agents.vendedor import build_vendedor_agent
        return build_vendedor_agent(**kwargs)


def _build_guard():
    from zwaf.security.guard import InputGuard
    return InputGuard()


def _make_purchase_history_fn(db_url: str, tenant_id: str) -> Callable[[str], bool]:
    """
    Retorna funcao sincrona que verifica se um numero tem historico de compra.
    Consulta payment_events para determinar se o lead ja comprou.
    Usa asyncpg com conexao por chamada (baixa frequencia — so em greetings).
    """
    import asyncio

    async def _async_check(phone: str) -> bool:
        if not db_url:
            return False
        clean_url = db_url.replace("+asyncpg", "")
        try:
            import asyncpg
            conn = await asyncpg.connect(clean_url)
            try:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM payment_events WHERE tenant_id=$1 AND lead_phone=$2 AND status='PAID'",
                    tenant_id,
                    phone,
                )
                return (count or 0) > 0
            finally:
                await conn.close()
        except Exception as e:
            logger.warning("purchase_history_fn failed: %s", e)
            return False

    def has_purchase_history(phone: str) -> bool:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Dentro de contexto async — cria task e bloqueia de forma segura
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _async_check(phone))
                    return future.result(timeout=3.0)
            return asyncio.run(_async_check(phone))
        except Exception:
            return False

    return has_purchase_history


def build_team(
    tenant_config: TenantConfig,
    db_url: str = "",
) -> ZWAFTeam:
    """
    Factory principal: constroi o ZWAFTeam completo para um tenant.
    Inicializa WhatsAppTool, RouterAgent, FidelizacaoScheduler.
    """
    # WhatsApp tool
    if tenant_config.whatsapp.phone_numbers:
        whatsapp = WhatsAppTool.from_phone_entries(
            entries=tenant_config.whatsapp.phone_numbers,
            api_key=tenant_config.whatsapp.evolution_api_key,
            base_url=tenant_config.whatsapp.evolution_api_url,
            messages_per_minute=tenant_config.whatsapp.messages_per_minute,
            typing_simulation=tenant_config.whatsapp.typing_simulation.enabled,
            typing_min_ms=tenant_config.whatsapp.typing_simulation.min_ms,
            typing_max_ms=tenant_config.whatsapp.typing_simulation.max_ms,
            typing_chars_per_second=tenant_config.whatsapp.typing_simulation.chars_per_second,
            typing_jitter_ms=tenant_config.whatsapp.typing_simulation.jitter_ms,
            send_text_delay_ms=tenant_config.whatsapp.send_text_delay_ms,
            warm_up_mode=tenant_config.whatsapp.warm_up_mode,
            warm_up_day=tenant_config.whatsapp.current_warm_up_day,
        )
    else:
        whatsapp = WhatsAppTool(
            api_key=tenant_config.whatsapp.evolution_api_key,
            base_url=tenant_config.whatsapp.evolution_api_url,
            messages_per_minute=tenant_config.whatsapp.messages_per_minute,
            typing_simulation=tenant_config.whatsapp.typing_simulation.enabled,
            typing_min_ms=tenant_config.whatsapp.typing_simulation.min_ms,
            typing_max_ms=tenant_config.whatsapp.typing_simulation.max_ms,
            typing_chars_per_second=tenant_config.whatsapp.typing_simulation.chars_per_second,
            typing_jitter_ms=tenant_config.whatsapp.typing_simulation.jitter_ms,
            send_text_delay_ms=tenant_config.whatsapp.send_text_delay_ms,
        )

    # Router com purchase_history_fn para detectar clientes recorrentes
    purchase_history_fn = _make_purchase_history_fn(db_url, tenant_config.tenant_id)
    router = RouterAgent(
        config=tenant_config.router,
        purchase_history_fn=purchase_history_fn,
    )

    # FidelizacaoScheduler — inicia se configurado no tenant
    fidelizacao_scheduler = None
    if tenant_config.fidelizacao:
        from zwaf.agents.fidelizacao import FidelizacaoScheduler
        fidelizacao_scheduler = FidelizacaoScheduler(
            tenant_config=tenant_config,
            whatsapp_tool=whatsapp,
            db_url=db_url,
        )
        fidelizacao_scheduler.start()
        logger.info(
            "FidelizacaoScheduler started for tenant %s (trigger_days=%d)",
            tenant_config.tenant_id,
            tenant_config.fidelizacao.get("trigger_days_after_purchase", 30),
        )

    team = ZWAFTeam(
        tenant_config=tenant_config,
        whatsapp_tool=whatsapp,
        router=router,
        db_url=db_url,
        fidelizacao_scheduler=fidelizacao_scheduler,
    )
    return team
