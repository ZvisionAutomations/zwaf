"""
ZWAFTeam — Coordenador multi-agente do ZWAF.

Orquestra o RouterAgent + 5 agentes especializados.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from zwaf.conversion.checkout_flow import advance_checkout, build_transition_message
from zwaf.conversion.checkout_policy import is_opt_out_message
from zwaf.conversion.intelligence import ConversionAction, LeadSignal, analyze_message
from zwaf.core.router_agent import RouterAgent, RouteResult
from zwaf.core.tenant import TenantConfig
from zwaf.memory.lead_store import append_conversion_event, mark_opt_out
from zwaf.memory.session import get_session_state, set_session_state
from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.core.team")

# Story-041: limite de turnos no modo checkout deterministico antes de desistir e
# voltar ao atendimento normal (rede de seguranca contra cliente preso na coleta).
MAX_CHECKOUT_ATTEMPTS = 4

# Quantidade de potes/unidades extraida da mensagem-gatilho ("quero 3 potes").
_QUANTITY_RE = re.compile(r"(\d+)\s*(?:potes?|unidades?|frascos?|caixas?|pote)", re.IGNORECASE)


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

        # 2. Conversion signal
        conversion_signal = analyze_message(
            guard_result.sanitized_input,
            tenant_id=self._tenant.tenant_id,
        )

        # 2.5 Checkout deterministico (story-041): se ja estamos coletando dados ou
        # o lead acabou de confirmar a compra, conduzir a coleta SEM o LLM. Tira o
        # ponto mais sensivel da venda do caminho do modelo.
        checkout_reply = await self._handle_checkout(
            message=guard_result.sanitized_input,
            phone=phone,
            session_id=session_id,
            lead_id=lead_id,
            signal=conversion_signal,
        )
        if checkout_reply is not None:
            await append_conversion_event(
                phone=phone,
                tenant_id=self._tenant.tenant_id,
                session_id=session_id,
                lead_id=lead_id,
                agent_name="checkout",
                signal=conversion_signal.to_dict(),
            )
            return TeamResponse(
                response=checkout_reply,
                agent_used="checkout",
                session_id=session_id,
                lead_id=lead_id,
                latency_ms=(time.monotonic() - start) * 1000,
                route_result=RouteResult("checkout", 1.0),
                conversion_signal=conversion_signal,
            )

        # 3. Route
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
            phone=phone,
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
        phone: str = "",
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

        # Story-040 FR-7: anti-loop de endereco. Se a falha foi de endereco e o
        # contador por sessao atingiu o threshold (>= 2), em vez de reenviar a
        # mensagem literal (que cria o loop do Fernando), escala ao humano e envia
        # a mensagem de transicao. CPF/erros criticos (035) NAO entram aqui porque
        # so falhas de endereco gravam "address_attempts" no sink.
        deterministic = payment_sink.get("deterministic_reply")
        if deterministic and _should_escalate_address(payment_sink, session_id, lead_id):
            return await self._escalate_address_loop(
                phone=phone,
                message=message,
                session_id=session_id,
                lead_id=lead_id,
            )

        # Bypass deterministico: se a tool de checkout devolveu uma mensagem
        # (qualquer retorno que nao seja uma URL de pagamento), envie-a literal.
        if deterministic:
            return deterministic
        return llm_reply

    async def _escalate_address_loop(
        self,
        phone: str,
        message: str,
        session_id: str,
        lead_id: str,
    ) -> str:
        """Escala ao humano apos 2 falhas de endereco e devolve msg de transicao."""
        from zwaf.conversion.address_attempts import reset_attempts
        from zwaf.tools.escalation import escalate_to_human

        lead_name = ""
        try:
            from zwaf.memory.lead_store import get_lead

            lead = await get_lead(phone=phone, tenant_id=self._tenant.tenant_id)
            if lead:
                lead_name = str(lead.get("name") or "")
        except Exception:  # nunca travar a escala por falta de nome
            lead_name = ""

        try:
            await escalate_to_human(
                lead_phone=phone,
                lead_name=lead_name or "Cliente",
                problem_summary=(
                    "Checkout travado: nao foi possivel completar o endereco de "
                    "entrega apos 2 tentativas (anti-loop story-040)."
                ),
                conversation_history=message,
            )
        except Exception as exc:
            logger.error(
                "Address-loop escalation failed",
                extra={"session_id": session_id, "error": str(exc)},
            )
        finally:
            # Zera o contador para nao reescalar a cada turno seguinte.
            reset_attempts(session_id, lead_id)

        logger.info(
            "Address checkout escalated to human (anti-loop)",
            extra={"session_id": session_id, "lead_id": lead_id},
        )
        return (
            "Vou te conectar com nossa equipe para finalizar seu pedido com "
            "calma. Ja estou chamando uma pessoa para te ajudar pessoalmente."
        )

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

    # ─── Checkout deterministico (story-041) ──────────────

    async def _handle_checkout(
        self,
        *,
        message: str,
        phone: str,
        session_id: str,
        lead_id: str,
        signal: LeadSignal,
    ) -> Optional[str]:
        """Conduz a coleta deterministica de checkout (story-041).

        Retorna a mensagem a enviar (literal) quando o fluxo de checkout esta no
        controle, ou None para deixar o atendimento normal (LLM) seguir.
        """
        state = await get_session_state(session_id, self._tenant.tenant_id)
        checkout = dict(state.get("checkout") or {})

        if checkout.get("active"):
            # HIGH-1 (story-041): um sinal critico (risco de saude, raiva/reclamacao
            # grave) durante a coleta NAO pode ser tratado como dado de checkout.
            # Sai do modo checkout e escala ao humano. Reusa a inteligencia
            # deterministica (`analyze_message` ja classifica esses casos como
            # ESCALATE_HUMAN — health-risk/anger). Campos do formulario
            # (Nome/CPF/CEP/Numero) nunca casam esses padroes, entao a coleta
            # normal nao e afetada.
            if signal.action == ConversionAction.ESCALATE_HUMAN:
                checkout["active"] = False
                state["checkout"] = checkout
                await set_session_state(session_id, self._tenant.tenant_id, state)
                return await self._escalate_from_checkout(
                    phone=phone,
                    message=message,
                    session_id=session_id,
                    lead_id=lead_id,
                    signal=signal,
                )
            return await self._continue_checkout(
                message=message,
                phone=phone,
                session_id=session_id,
                state=state,
                checkout=checkout,
            )

        # HIGH-2 (story-041): lembrar a ultima quantidade mencionada ANTES de o
        # checkout ativar. O cliente costuma dizer "quero 3 potes" e so depois
        # "pode mandar o pix" (mensagem-gatilho sem quantidade) — sem isso a qty
        # caia para 1 e cobraria o valor errado.
        mentioned_qty = _quantity_in_message(message)
        if mentioned_qty is not None and not signal.should_send_payment_link:
            if state.get("last_quantity") != mentioned_qty:
                state["last_quantity"] = mentioned_qty
                await set_session_state(session_id, self._tenant.tenant_id, state)

        # Nao ativo: inicia a coleta apenas quando ha intencao de compra explicita
        # (mesma regra deterministica que hoje libera o link).
        if signal.should_send_payment_link:
            quantity = mentioned_qty or int(state.get("last_quantity", 1) or 1)
            checkout = {
                "active": True,
                "product_id": self._default_product_id(signal.product_hint),
                "billing_type": "PIX",
                "quantity": quantity,
                "fields": {},
                "attempts": 0,
            }
            state["checkout"] = checkout
            await set_session_state(session_id, self._tenant.tenant_id, state)
            return build_transition_message(quantity)

        return None

    async def _escalate_from_checkout(
        self,
        *,
        phone: str,
        message: str,
        session_id: str,
        lead_id: str,
        signal: LeadSignal,
    ) -> str:
        """Escala ao humano um sinal critico recebido durante o checkout (HIGH-1)."""
        from zwaf.tools.escalation import escalate_to_human

        lead_name = ""
        try:
            from zwaf.memory.lead_store import get_lead

            lead = await get_lead(phone=phone, tenant_id=self._tenant.tenant_id)
            if lead:
                lead_name = str(lead.get("name") or "")
        except Exception:  # nunca travar a escala por falta de nome
            lead_name = ""

        reason = (signal.reasons[0] if signal.reasons else "Sinal critico durante o checkout")
        try:
            confirmation = await escalate_to_human(
                lead_phone=phone,
                lead_name=lead_name or "Cliente",
                problem_summary=(
                    f"Sinal critico durante o checkout ({signal.sentiment.value}): "
                    f"{reason}. Coleta de dados interrompida para atendimento humano."
                ),
                conversation_history=message,
            )
        except Exception as exc:
            logger.error(
                "Checkout escalation failed",
                extra={"session_id": session_id, "error": str(exc)},
            )
            confirmation = (
                "Vou te conectar agora com nossa equipe para te ajudar pessoalmente."
            )

        logger.info(
            "Critical signal during checkout escalated to human",
            extra={
                "session_id": session_id,
                "lead_id": lead_id,
                "sentiment": signal.sentiment.value,
            },
        )
        return confirmation

    async def _continue_checkout(
        self,
        *,
        message: str,
        phone: str,
        session_id: str,
        state: dict,
        checkout: dict,
    ) -> str:
        """Processa um turno de coleta: avanca o fluxo, gera o Pix ou pede o que falta."""
        turn = await advance_checkout(message, checkout.get("fields", {}))
        checkout["fields"] = turn.collected

        if turn.ready:
            reply = await self._generate_pix_for_checkout(
                phone=phone,
                product_id=checkout.get("product_id", ""),
                billing_type=checkout.get("billing_type", "PIX"),
                quantity=int(checkout.get("quantity", 1) or 1),
                collected=turn.collected,
                resolved_address=turn.resolved_address,
            )
            # Encerra o modo checkout — gerado ou nao, a coleta acabou.
            checkout["active"] = False
            state["checkout"] = checkout
            await set_session_state(session_id, self._tenant.tenant_id, state)
            return reply

        # Ainda coletando: incrementa tentativas e aplica a rede de seguranca para
        # nao prender o cliente no modo checkout indefinidamente.
        checkout["attempts"] = int(checkout.get("attempts", 0)) + 1
        if checkout["attempts"] >= MAX_CHECKOUT_ATTEMPTS:
            checkout["active"] = False
            state["checkout"] = checkout
            await set_session_state(session_id, self._tenant.tenant_id, state)
            logger.info(
                "Checkout collection gave up after %d attempts — back to normal flow",
                checkout["attempts"],
                extra={"session_id": session_id},
            )
            return (
                "Vou te ajudar a finalizar com calma. Pode me mandar os dados que "
                "faltam quando puder, ou me chama que eu te oriento passo a passo."
            )
        state["checkout"] = checkout
        await set_session_state(session_id, self._tenant.tenant_id, state)
        return turn.reply

    async def _generate_pix_for_checkout(
        self,
        *,
        phone: str,
        product_id: str,
        billing_type: str,
        quantity: int,
        collected: dict,
        resolved_address: dict,
    ) -> str:
        """Gera a cobranca/Pix a partir dos dados ja coletados e validados."""
        from zwaf.tools.payment import make_payment_link_generator

        generator = make_payment_link_generator(self._tenant.tenant_id, self._tenant.payment)
        # Quantidade pode vir tambem no formulario rotulado ("Quantidade: 3").
        qty = collected.get("quantity")
        try:
            quantity = int(qty) if qty else quantity
        except (TypeError, ValueError):
            pass
        return await generator(
            product_id=product_id,
            customer_phone=phone,
            customer_name=collected.get("name", ""),
            customer_document=collected.get("document", ""),
            delivery_address=resolved_address,
            billing_type=billing_type,
            quantity=max(1, int(quantity or 1)),
        )

    def _default_product_id(self, product_hint: Optional[str]) -> str:
        """Resolve o produto do checkout: hint do sinal, unico produto do tenant, ou new-woman."""
        if product_hint:
            return product_hint
        try:
            products = (self._tenant.payment or {}).get("products", {})
        except AttributeError:
            products = {}
        if len(products) == 1:
            return next(iter(products))
        return "new-woman"


def _quantity_in_message(message: str) -> Optional[int]:
    """Quantidade de potes/unidades explicita na mensagem, ou None se ausente.

    Distingue "nao mencionou quantidade" (None) de "mencionou 1" — essencial para
    o HIGH-2: so sobrescreve a quantidade lembrada quando o cliente realmente
    informa um numero.
    """
    match = _QUANTITY_RE.search(message or "")
    if match:
        try:
            return max(1, int(match.group(1)))
        except (TypeError, ValueError):
            return None
    return None


def _extract_quantity(message: str) -> int:
    """Extrai a quantidade de potes/unidades da mensagem-gatilho. Default 1."""
    return _quantity_in_message(message) or 1


def _should_escalate_address(
    payment_sink: dict[str, Any],
    session_id: str,
    lead_id: str,
) -> bool:
    """True quando a falha foi de endereco e o anti-loop atingiu o threshold.

    Story-040 FR-7. So escala quando o sink registrou "address_attempts" (i.e. a
    falha deterministica foi puramente de endereco — CPF/erros criticos da 035
    nao gravam essa chave e seguem o bypass literal) E o contador por sessao
    chegou ao threshold (>= 2). Funcao pura para testar offline sem agno.
    """
    if "address_attempts" not in payment_sink:
        return False
    from zwaf.conversion.address_attempts import should_escalate

    return should_escalate(session_id, lead_id)


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
