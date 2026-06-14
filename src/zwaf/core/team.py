"""
ZWAFTeam — Coordenador multi-agente do ZWAF.

Orquestra o RouterAgent + 5 agentes especializados.
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional

from zwaf.conversion.checkout_flow import advance_checkout, build_transition_message
from zwaf.conversion.checkout_policy import is_opt_out_message
from zwaf.conversion.funnel_events import FunnelEventName, build_funnel_event
from zwaf.conversion.intelligence import BuyingIntent, ConversionAction, LeadSignal, analyze_message
from zwaf.conversion.self_improvement import ImprovementKind, ImprovementQueue
from zwaf.observability import langfuse as _obs
from zwaf.core.router_agent import RouterAgent, RouteResult
from zwaf.core.tenant import TenantConfig
from zwaf.memory.lead_memory import build_memory_block, maybe_update_lead_memory
from zwaf.memory.lead_store import append_conversion_event, mark_opt_out
from zwaf.memory.session import (
    acquire_session_lock,
    get_session_state,
    release_session_lock,
    set_session_state,
)
from zwaf.security.pii import can_encrypt_pii, decrypt_pii, encrypt_pii
from zwaf.tools.payment import (
    MESSAGE_SPLIT,
    _format_brl,
    _resolve_product_and_qty,
    _tier_unit_cents,
    _total_cents,
)
from zwaf.tools.whatsapp import WhatsAppTool

logger = logging.getLogger("zwaf.core.team")

# Story-041: limite de turnos no modo checkout deterministico antes de desistir e
# voltar ao atendimento normal (rede de seguranca contra cliente preso na coleta).
MAX_CHECKOUT_ATTEMPTS = 4

# Story-044: agentes cuja conversa alimenta o summarizer de memória de lead.
_SUMMARIZABLE_AGENTS = {"vendedor", "recompra", "suporte", "cobranca"}

CHECKOUT_PAYMENT_LOCK_NAME = "checkout_pix"  # nome do lock mantido por compat. Redis
CHECKOUT_PAYMENT_LOCK_TTL_SECONDS = 20
CHECKOUT_FIELDS_ENCRYPTED_FLAG = "_pii_encrypted"
CHECKOUT_SENSITIVE_FIELDS = {
    "name",
    "document",
    "postal_code",
    "number",
    "complement",
    "street",
    "district",
    "city",
    "state",
}

# Quantidade de potes/unidades (story-046). Dois caminhos SEGUROS de deteccao:
#  (1) numero + substantivo de unidade ("3 potes", "dois frascos") — inequivoco;
#  (2) numero colado a um verbo de compra ("quero 2", "vou levar dois") — guardado
#      contra parcelamento ("2 vezes"/"2x") e contra numeros de outros campos (CEP,
#      numero da casa, CPF, "faz 2 anos"), que nunca trazem verbo/unidade colado.
_SPELLED_NUMBERS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
}
_NUM_TOKEN = r"\d+|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez"
_QTY_UNIT_RE = re.compile(
    rf"\b({_NUM_TOKEN})\s*(?:potes?|pote|unidades?|frascos?|caixas?|kits?|vidros?)\b",
    re.IGNORECASE,
)
_QTY_BUY_RE = re.compile(
    rf"\b(?:quero|queria|vou\s+querer|vou\s+levar|lev(?:ar|o|a)|manda(?:r)?|mande|"
    rf"me\s+v[eê]|pega(?:r)?|coloca(?:r)?|p[oõ]e)\s+"
    rf"(?:mais\s+|s[oó]\s+|uns\s+|umas\s+)?({_NUM_TOKEN})\b"
    rf"(?!\s*(?:vez(?:es)?|x|%|por\s*cento|ano|anos|m[eê]s|meses|dia|dias))",
    re.IGNORECASE,
)
# Numero solto (digito ou por extenso) — usado SO ao ler a resposta a pergunta de
# quantidade, onde o contexto ja e "quantos potes?".
_NUM_TOKEN_RE = re.compile(rf"\b({_NUM_TOKEN})\b", re.IGNORECASE)

# Story-042: deteccao deterministica do meio de pagamento na mensagem do cliente.
# Cartao/parcelar -> CREDIT_CARD; pix -> PIX. Default e PIX (maior conversao).
_CARD_RE = re.compile(r"\b(cart[aã]o|cr[eé]dito|parcel)", re.IGNORECASE)
_PIX_RE = re.compile(r"\bpix\b", re.IGNORECASE)


@dataclass
class TeamResponse:
    response: str
    agent_used: str
    session_id: str
    lead_id: str
    latency_ms: float
    route_result: RouteResult
    conversion_signal: Optional[LeadSignal] = None


def _decrypt_checkout_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a session state copy with checkout fields decrypted when needed."""
    result = deepcopy(state or {})
    checkout = result.get("checkout")
    if not isinstance(checkout, dict):
        return result
    fields = checkout.get("fields")
    if not isinstance(fields, dict) or not fields.get(CHECKOUT_FIELDS_ENCRYPTED_FLAG):
        return result

    decrypted: dict[str, Any] = {}
    for key, value in fields.items():
        if key == CHECKOUT_FIELDS_ENCRYPTED_FLAG:
            continue
        if key in CHECKOUT_SENSITIVE_FIELDS and isinstance(value, str) and can_encrypt_pii():
            decrypted[key] = decrypt_pii(value)
        else:
            decrypted[key] = value
    checkout["fields"] = decrypted
    return result


def _encrypt_checkout_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a session state copy with checkout PII encrypted for Redis."""
    result = deepcopy(state or {})
    if not can_encrypt_pii():
        return result
    checkout = result.get("checkout")
    if not isinstance(checkout, dict):
        return result
    fields = checkout.get("fields")
    if not isinstance(fields, dict) or fields.get(CHECKOUT_FIELDS_ENCRYPTED_FLAG):
        return result

    encrypted: dict[str, Any] = {}
    for key, value in fields.items():
        if key in CHECKOUT_SENSITIVE_FIELDS and isinstance(value, str) and value:
            encrypted[key] = encrypt_pii(value)
        else:
            encrypted[key] = value
    encrypted[CHECKOUT_FIELDS_ENCRYPTED_FLAG] = True
    checkout["fields"] = encrypted
    return result


async def _save_session_state(
    session_id: str,
    tenant_id: str,
    state: dict[str, Any],
) -> None:
    await set_session_state(session_id, tenant_id, _encrypt_checkout_state(state))


def _emit_event(
    event: FunnelEventName,
    phone: str,
    tenant_id: str,
    metadata: dict | None = None,
) -> None:
    """Fire a PII-safe funnel event — best-effort, never raises."""
    try:
        fe = build_funnel_event(
            event=event,
            session_id=phone,
            tenant_id=tenant_id,
            metadata=metadata or {},
        )
        _obs.emit_funnel_event(fe)
    except Exception:
        pass


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
        inventory_sweep_scheduler=None,
        lead_memory_retention_scheduler=None,
    ):
        self._tenant = tenant_config
        self._whatsapp = whatsapp_tool
        self._router = router
        self._db_url = db_url
        self._guard = _build_guard()
        self._improvement_queue = ImprovementQueue(
            db_url=os.getenv("DATABASE_URL"),
            tenant_id=tenant_config.tenant_id,
        )
        # Referencia ao scheduler para shutdown no lifespan
        self._fidelizacao_scheduler = fidelizacao_scheduler
        self._inventory_sweep_scheduler = inventory_sweep_scheduler
        self._lead_memory_retention_scheduler = lead_memory_retention_scheduler

    async def send_response(self, phone: str, text: str, session_id: str) -> None:
        """Envia resposta via WhatsApp — interface publica para o webhook.

        Se a resposta contiver MESSAGE_SPLIT, ela e quebrada em varias mensagens
        (ex.: Pix = anuncio + codigo puro), enviadas em sequencia. Cada parte
        passa pela simulacao de digitacao/rate-limit do WhatsAppTool.
        """
        parts = text.split(MESSAGE_SPLIT) if (text and MESSAGE_SPLIT in text) else [text]
        for part in parts:
            chunk = part.strip()
            if chunk:
                await self._whatsapp.send_message(phone=phone, text=chunk, session_id=session_id)

    def _suggest_improvement(
        self,
        kind: ImprovementKind,
        summary: str,
        evidence: dict | None = None,
    ) -> None:
        try:
            self._improvement_queue.suggest(
                kind=kind,
                summary=summary,
                evidence=evidence or {},
            )
        except Exception:
            pass

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
            self._suggest_improvement(
                ImprovementKind.SEGMENTATION,
                "Lead solicitou opt-out — revisar segmentacao ou abordagem de campanha",
            )
            _emit_event(FunnelEventName.OPT_OUT, phone, self._tenant.tenant_id)
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
        # Funnel: diagnosis after every analyzed turn.
        if conversion_signal.buying_intent != BuyingIntent.NONE:
            _emit_event(
                FunnelEventName.DIAGNOSIS_COMPLETED,
                phone,
                self._tenant.tenant_id,
                {"lead_temperature": conversion_signal.buying_intent.value},
            )
        if conversion_signal.action == ConversionAction.HANDLE_OBJECTION:
            _emit_event(
                FunnelEventName.OBJECTION_DETECTED,
                phone,
                self._tenant.tenant_id,
                {"objection": conversion_signal.objection or ""},
            )
            self._suggest_improvement(
                ImprovementKind.COPY,
                f"Objecao detectada: {conversion_signal.objection or 'nao especificada'}",
                {"objection": conversion_signal.objection or ""},
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

        # Funnel: offer presented when the sales agent responds with medium+ intent.
        if route.agent_name == "vendedor" and conversion_signal.buying_intent in (
            BuyingIntent.MEDIUM,
            BuyingIntent.HIGH,
        ):
            _emit_event(FunnelEventName.OFFER_PRESENTED, phone, self._tenant.tenant_id)

        # 3. Execute agent — monta a memoria do lead (story-044), so com a flag ON.
        lead_memory_block = await self._build_lead_memory_block(session_id, phone)
        response_text = await self._run_agent(
            agent_name=route.agent_name,
            message=guard_result.sanitized_input,
            session_id=session_id,
            lead_id=lead_id,
            phone=phone,
            lead_memory_block=lead_memory_block,
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

    async def _build_lead_memory_block(self, session_id: str, phone: str) -> str:
        """Monta o bloco de memoria do lead (story-044).

        Retorna '' quando a feature flag esta desligada (comportamento atual,
        regressao zero) ou quando o lead nao tem relacao previa. Nunca propaga
        excecao — falha de memoria nao pode travar a venda.
        """
        cfg = getattr(self._tenant, "lead_memory", None) or {}
        if not cfg.get("enabled"):
            return ""
        try:
            state = await get_session_state(session_id, self._tenant.tenant_id)
            max_chars = int(cfg.get("reinject_max_chars", 1000) or 1000)
            return await build_memory_block(
                phone,
                self._tenant.tenant_id,
                session_state=state,
                max_chars=max_chars,
            )
        except Exception as e:
            logger.warning("lead memory block build failed: %s", e)
            return ""

    async def update_lead_memory(self, *, phone: str, session_id: str, agent_name: str) -> bool:
        """Dispara o summarizer pós-resposta (story-044, F3).

        Chamado pelo webhook via asyncio.create_task APÓS o envio da resposta —
        fora do caminho quente. Best-effort: a flag e o throttle são decididos
        dentro de maybe_update_lead_memory; nunca propaga exceção.
        """
        name = agent_name if agent_name in _SUMMARIZABLE_AGENTS else "vendedor"
        return await maybe_update_lead_memory(
            phone=phone,
            tenant_id=self._tenant.tenant_id,
            session_id=session_id,
            agent_name=name,
            tenant_config=self._tenant,
            db_url=self._db_url,
        )

    async def _run_agent(
        self,
        agent_name: str,
        message: str,
        session_id: str,
        lead_id: str,
        phone: str = "",
        lead_memory_block: str = "",
    ) -> str:
        """Constroi e executa o agente especializado com fallback."""
        # Sink por-request: a tool de pagamento registra aqui mensagens
        # deterministicas de checkout (ex.: CPF invalido, dado faltante). Quando
        # presente, ela e enviada LITERALMENTE ao cliente, sem parafrase do LLM —
        # garante consistencia mesmo com modelos que tendem a reescrever erros.
        payment_sink: dict[str, Any] = {}
        agent = self._build_agent(
            agent_name,
            session_id,
            lead_id,
            result_sink=payment_sink,
            lead_memory_block=lead_memory_block,
        )
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

        _emit_event(FunnelEventName.HANDOFF_TO_HUMAN, phone, self._tenant.tenant_id,
                    {"stage": "address_loop"})
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
        lead_memory_block: str = "",
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
            return build_vendedor_agent(
                payment_result_sink=result_sink,
                lead_memory_block=lead_memory_block,
                **kwargs,
            )
        if agent_name == "recompra":
            from zwaf.agents.recompra import build_recompra_agent
            return build_recompra_agent(lead_memory_block=lead_memory_block, **kwargs)
        if agent_name == "suporte":
            from zwaf.agents.suporte import build_suporte_agent
            return build_suporte_agent(lead_memory_block=lead_memory_block, **kwargs)
        if agent_name == "cobranca":
            from zwaf.agents.cobranca import build_cobranca_agent
            return build_cobranca_agent(lead_memory_block=lead_memory_block, **kwargs)

        if agent_name == "fidelizacao":
            logger.warning(
                "RouterAgent tentou rotear para 'fidelizacao' — agente nao e roteavel diretamente "
                "(FidelizacaoScheduler opera via cron, nao por mensagem). "
                "Redirecionando para vendedor."
            )
            from zwaf.agents.vendedor import build_vendedor_agent
            return build_vendedor_agent(
                payment_result_sink=result_sink,
                lead_memory_block=lead_memory_block,
                **kwargs,
            )

        logger.warning("Unknown agent name '%s' — defaulting to vendedor", agent_name)
        from zwaf.agents.vendedor import build_vendedor_agent
        return build_vendedor_agent(lead_memory_block=lead_memory_block, **kwargs)

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
        state = _decrypt_checkout_state(
            await get_session_state(session_id, self._tenant.tenant_id)
        )
        # Funnel: fire conversation_started once per lead session.
        if not state.get("_funnel_started"):
            _emit_event(FunnelEventName.CONVERSATION_STARTED, phone, self._tenant.tenant_id)
            state["_funnel_started"] = True
            await _save_session_state(session_id, self._tenant.tenant_id, state)

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
                await _save_session_state(session_id, self._tenant.tenant_id, state)
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

        # Story-046: se ha uma pergunta de quantidade/meio pendente (gate pre-coleta),
        # a resposta do cliente e processada aqui antes de qualquer outra coisa.
        if state.get("pending_checkout"):
            resolved = await self._advance_precheckout(
                phone=phone,
                message=message,
                session_id=session_id,
                state=state,
                pending=dict(state["pending_checkout"]),
            )
            if resolved is not None:
                return resolved

        # HIGH-2 (story-041): lembrar a ultima quantidade mencionada ANTES de o
        # checkout ativar. O cliente costuma dizer "quero 3 potes" e so depois
        # "pode mandar o pix" (mensagem-gatilho sem quantidade) — sem isso a qty
        # caia para 1 e cobraria o valor errado.
        mentioned_qty = _quantity_in_message(message)
        if mentioned_qty is not None and not signal.should_send_payment_link:
            if state.get("last_quantity") != mentioned_qty:
                state["last_quantity"] = mentioned_qty
                await _save_session_state(session_id, self._tenant.tenant_id, state)

        # Story-042: lembrar o ultimo meio de pagamento mencionado, igual a qty.
        # O cliente diz "quero pagar no cartao" e so depois "pode fechar".
        mentioned_billing = _detect_billing_type(message)
        if mentioned_billing is not None and not signal.should_send_payment_link:
            if state.get("last_billing_type") != mentioned_billing:
                state["last_billing_type"] = mentioned_billing
                await _save_session_state(session_id, self._tenant.tenant_id, state)

        # Track objections for future OBJECTION_RESOLVED detection.
        if signal.action == ConversionAction.HANDLE_OBJECTION and not state.get("_had_objection"):
            state["_had_objection"] = True
            await _save_session_state(session_id, self._tenant.tenant_id, state)

        # Nao ativo: inicia a coleta apenas quando ha intencao de compra explicita.
        # Story-046: se a quantidade NAO foi decidida, ancora 2-vs-1 antes de assumir
        # 1; se o meio NAO foi escolhido, pergunta "cartao ou Pix?" — tudo antes da
        # coleta de dados. Se ambos ja estao decididos, vai direto para a coleta.
        if signal.should_send_payment_link:
            if state.get("_had_objection"):
                _emit_event(FunnelEventName.OBJECTION_RESOLVED, phone, self._tenant.tenant_id)
            _emit_event(FunnelEventName.CHECKOUT_REQUESTED, phone, self._tenant.tenant_id)
            quantity = (
                mentioned_qty if mentioned_qty is not None else state.get("last_quantity")
            )
            billing_type = mentioned_billing or state.get("last_billing_type")
            return await self._begin_checkout_or_prompt(
                phone=phone,
                session_id=session_id,
                state=state,
                quantity=quantity,
                billing_type=billing_type,
                product_hint=signal.product_hint,
            )

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

        _emit_event(FunnelEventName.HANDOFF_TO_HUMAN, phone, self._tenant.tenant_id,
                    {"stage": "checkout_escalation"})
        self._suggest_improvement(
            ImprovementKind.TEMPLATE,
            f"Escala critica durante checkout: {reason}",
            {"sentiment": signal.sentiment.value, "reason": reason},
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
        """Processa um turno de coleta: avanca o fluxo, gera o pagamento ou pede o que falta."""
        # Story-042: se o cliente trocar de meio durante a coleta ("na verdade no
        # cartao"), respeita a ultima escolha — os dados coletados continuam validos.
        switched_billing = _detect_billing_type(message)
        if switched_billing is not None:
            checkout["billing_type"] = switched_billing

        # Story-046: se o cliente corrigir a quantidade durante a coleta ("quero 2"),
        # atualiza, RE-CONFIRMA o novo total e NAO gera neste turno — fecha a janela
        # em que a qty/valor antigo seria cobrado em silencio (caso Loma). Os dados
        # desta mensagem ainda sao aproveitados para nao perder nada.
        switched_qty = _quantity_in_message(message)
        current_qty = int(checkout.get("quantity", 1) or 1)
        if switched_qty is not None and switched_qty != current_qty:
            checkout["quantity"] = switched_qty
            turn = await advance_checkout(message, checkout.get("fields", {}))
            checkout["fields"] = turn.collected
            state["checkout"] = checkout
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            confirm = self._reconfirm_total_message(checkout)
            if turn.ready:
                return f"{confirm} Me confirma que eu ja te mando o link."
            return f"{confirm}\n\n{turn.reply}" if turn.reply else confirm

        if checkout.get("billing_type") == "CREDIT_CARD":
            checkout["active"] = False
            state["checkout"] = checkout
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            return await self._generate_payment_for_checkout_with_lock(
                phone=phone,
                session_id=session_id,
                product_id=checkout.get("product_id", ""),
                billing_type="CREDIT_CARD",
                quantity=int(checkout.get("quantity", 1) or 1),
                collected={},
                resolved_address={},
            )

        turn = await advance_checkout(message, checkout.get("fields", {}))
        checkout["fields"] = turn.collected

        if turn.ready:
            billing_type = checkout.get("billing_type", "PIX")
            reply = await self._generate_payment_for_checkout_with_lock(
                phone=phone,
                session_id=session_id,
                product_id=checkout.get("product_id", ""),
                billing_type=billing_type,
                quantity=int(checkout.get("quantity", 1) or 1),
                collected=turn.collected,
                resolved_address=turn.resolved_address,
            )
            # Encerra o modo checkout — gerado ou nao, a coleta acabou.
            checkout["active"] = False
            state["checkout"] = checkout
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            return reply

        # Ainda coletando: incrementa tentativas e aplica a rede de seguranca para
        # nao prender o cliente no modo checkout indefinidamente.
        checkout["attempts"] = int(checkout.get("attempts", 0)) + 1
        if checkout["attempts"] >= MAX_CHECKOUT_ATTEMPTS:
            checkout["active"] = False
            state["checkout"] = checkout
            await _save_session_state(session_id, self._tenant.tenant_id, state)
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
        await _save_session_state(session_id, self._tenant.tenant_id, state)
        return turn.reply

    async def _generate_payment_for_checkout_with_lock(
        self,
        *,
        phone: str,
        session_id: str,
        product_id: str,
        billing_type: str,
        quantity: int,
        collected: dict,
        resolved_address: dict,
    ) -> str:
        lock_acquired = await acquire_session_lock(
            tenant_id=self._tenant.tenant_id,
            session_id=session_id,
            lock_name=CHECKOUT_PAYMENT_LOCK_NAME,
            ttl_seconds=CHECKOUT_PAYMENT_LOCK_TTL_SECONDS,
        )
        if not lock_acquired:
            logger.info(
                "Checkout payment generation already in progress",
                extra={"session_id": session_id, "tenant_id": self._tenant.tenant_id},
            )
            meio = "link de pagamento" if billing_type == "CREDIT_CARD" else "Pix"
            return (
                f"Seu {meio} ja esta sendo gerado. Se ele nao aparecer em alguns "
                "segundos, me chama aqui que eu confiro para voce."
            )
        try:
            return await self._generate_payment_for_checkout(
                phone=phone,
                product_id=product_id,
                billing_type=billing_type,
                quantity=quantity,
                collected=collected,
                resolved_address=resolved_address,
            )
        finally:
            await release_session_lock(
                tenant_id=self._tenant.tenant_id,
                session_id=session_id,
                lock_name=CHECKOUT_PAYMENT_LOCK_NAME,
            )

    async def _generate_payment_for_checkout(
        self,
        *,
        phone: str,
        product_id: str,
        billing_type: str,
        quantity: int,
        collected: dict,
        resolved_address: dict,
    ) -> str:
        """Gera a cobranca (Pix copia-e-cola ou link de cartao) a partir dos dados
        ja coletados e validados. O `billing_type` decide o meio (story-042)."""
        from zwaf.tools.payment import make_payment_link_generator

        generator = make_payment_link_generator(self._tenant.tenant_id, self._tenant.payment)
        # Quantidade pode vir tambem no formulario rotulado ("Quantidade: 3").
        qty = collected.get("quantity")
        try:
            quantity = int(qty) if qty else quantity
        except (TypeError, ValueError):
            pass
        reply = await generator(
            product_id=product_id,
            customer_phone=phone,
            customer_name=collected.get("name", ""),
            customer_document=collected.get("document", ""),
            delivery_address=resolved_address,
            billing_type=billing_type,
            quantity=max(1, int(quantity or 1)),
        )
        _emit_event(
            FunnelEventName.CHECKOUT_LINK_GENERATED,
            phone,
            self._tenant.tenant_id,
            {"stage": billing_type or "PIX"},
        )
        return reply

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

    # ─── Pre-checkout: ancora de quantidade + escolha de meio (story-046) ──

    async def _begin_checkout_or_prompt(
        self,
        *,
        phone: str,
        session_id: str,
        state: dict,
        quantity: Optional[int],
        billing_type: Optional[str],
        product_hint: Optional[str],
    ) -> str:
        """Decide o passo antes da coleta: sem quantidade, ancora 2-vs-1; sem meio,
        pergunta cartao/Pix; com ambos decididos, inicia o checkout (Pix coleta no chat,
        cartao vai pro checkout hospedado — story-046 + story-048)."""
        if quantity is None:
            state["pending_checkout"] = {
                "stage": "quantity",
                "billing_type": billing_type or "",
                "product_hint": product_hint or "",
            }
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            return self._anchor_quantity_message(product_hint)
        if billing_type is None:
            state["pending_checkout"] = {
                "stage": "billing",
                "quantity": int(quantity),
                "product_hint": product_hint or "",
            }
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            return self._billing_question_message()
        return await self._start_checkout(
            phone=phone,
            session_id=session_id,
            state=state,
            quantity=int(quantity),
            billing_type=billing_type,
            product_hint=product_hint,
        )

    async def _advance_precheckout(
        self,
        *,
        phone: str,
        message: str,
        session_id: str,
        state: dict,
        pending: dict,
    ) -> Optional[str]:
        """Processa a resposta a uma pergunta pendente de quantidade/meio (story-046).

        Retorna a proxima mensagem (proxima pergunta ou transicao de coleta), ou None
        para largar o gate e deixar a Livia (LLM) conduzir quando a resposta nao for
        reconhecida como quantidade.
        """
        stage = pending.get("stage")
        if stage == "quantity":
            qty = _read_quantity_answer(message)
            if qty is None:
                state.pop("pending_checkout", None)
                await _save_session_state(session_id, self._tenant.tenant_id, state)
                return None
            billing = pending.get("billing_type") or _detect_billing_type(message)
            return await self._begin_checkout_or_prompt(
                phone=phone,
                session_id=session_id,
                state=state,
                quantity=qty,
                billing_type=billing or None,
                product_hint=pending.get("product_hint"),
            )
        if stage == "billing":
            # Default PIX quando a resposta nao deixa claro (maior conversao); nunca
            # trava a venda por causa da escolha do meio.
            billing = _detect_billing_type(message) or "PIX"
            return await self._start_checkout(
                phone=phone,
                session_id=session_id,
                state=state,
                quantity=int(pending.get("quantity", 1) or 1),
                billing_type=billing,
                product_hint=pending.get("product_hint"),
            )
        state.pop("pending_checkout", None)
        await _save_session_state(session_id, self._tenant.tenant_id, state)
        return None

    async def _start_checkout(
        self,
        *,
        phone: str,
        session_id: str,
        state: dict,
        quantity: int,
        billing_type: str,
        product_hint: Optional[str],
    ) -> str:
        """Inicia o checkout com a quantidade e o meio ja decididos.

        Story-048: CARTAO vai para o checkout HOSPEDADO do Asaas — os dados do cliente
        (nome/CPF/endereco) sao coletados na pagina, NAO no chat; nao ativa coleta.
        PIX (e demais) seguem a coleta deterministica no chat (story-041/046).
        """
        product_id = self._default_product_id(product_hint)
        qty = max(1, int(quantity or 1))
        if billing_type == "CREDIT_CARD":
            checkout = {
                "active": False,
                "product_id": product_id,
                "billing_type": billing_type,
                "quantity": qty,
                "fields": {},
                "attempts": 0,
            }
            state["checkout"] = checkout
            state.pop("pending_checkout", None)
            await _save_session_state(session_id, self._tenant.tenant_id, state)
            return await self._generate_payment_for_checkout_with_lock(
                phone=phone,
                session_id=session_id,
                product_id=product_id,
                billing_type=billing_type,
                quantity=qty,
                collected={},
                resolved_address={},
            )
        checkout = {
            "active": True,
            "product_id": product_id,
            "billing_type": billing_type,
            "quantity": qty,
            "fields": {},
            "attempts": 0,
        }
        state["checkout"] = checkout
        state.pop("pending_checkout", None)
        await _save_session_state(session_id, self._tenant.tenant_id, state)
        return build_transition_message(qty, billing_type)

    def _tenant_products(self) -> dict:
        return (getattr(self._tenant, "payment", None) or {}).get("products", {})

    def _anchor_quantity_message(self, product_hint: Optional[str]) -> str:
        """Ancora 2-vs-1 com a economia real do tenant (story-028 e a fonte do preco)."""
        products = self._tenant_products()
        pid = self._default_product_id(product_hint)
        cfg, _ = _resolve_product_and_qty(products, pid, 1)
        tiers = cfg.get("unit_price_tiers_pix_cents") or []
        unit1 = _tier_unit_cents(tiers, 1)
        unit2 = _tier_unit_cents(tiers, 2)
        total2 = _total_cents(cfg, 2, "PIX")
        if unit1 and unit2 and total2:
            return (
                "Posso te mandar o link! Pelo que a gente conversou, o ideal e o ciclo "
                f"completo: 2 potes saem {_format_brl(unit2)} cada ({_format_brl(total2)} "
                f"no total, frete gratis), em vez de {_format_brl(unit1)} no pote avulso. "
                "Mas da pra comecar com 1 se voce preferir sentir como seu corpo responde. "
                "Quer fechar os 2 ou comecar com 1?"
            )
        return (
            "Posso te mandar o link! A maioria comeca com o ciclo completo de 2 potes "
            "(sai mais em conta por pote e com frete gratis), mas da pra comecar com 1 "
            "tambem. Quer fechar os 2 ou comecar com 1?"
        )

    def _billing_question_message(self) -> str:
        return (
            "Perfeito! E qual a melhor forma pra voce: cartao de credito ou Pix? "
            "(No Pix o valor e o melhor; no cartao da pra parcelar.)"
        )

    def _reconfirm_total_message(self, checkout: dict) -> str:
        qty = int(checkout.get("quantity", 1) or 1)
        unidade = "pote" if qty == 1 else "potes"
        meio = "no cartao" if checkout.get("billing_type") == "CREDIT_CARD" else "no Pix"
        total = self._order_total_cents(checkout)
        if total:
            return f"Anotado! Fechamos {qty} {unidade} = {_format_brl(total)} {meio}."
        return f"Anotado, {qty} {unidade}!"

    def _order_total_cents(self, checkout: dict) -> Optional[int]:
        products = self._tenant_products()
        cfg, qty = _resolve_product_and_qty(
            products, checkout.get("product_id", ""), int(checkout.get("quantity", 1) or 1)
        )
        return _total_cents(cfg, qty, checkout.get("billing_type", "PIX"))


def _coerce_qty_token(token: str) -> Optional[int]:
    """Converte um token de quantidade (digito ou por extenso) para int 1-99, ou None."""
    token = (token or "").strip().lower()
    if token.isdigit():
        n = int(token)
        return n if 1 <= n <= 99 else None
    # Normaliza acentos para casar "tres"/"tres"-com-acento (story-046 MED-1):
    # o regex aceita "tr[eê]s", entao o lookup precisa cobrir a forma acentuada.
    norm = "".join(
        c for c in unicodedata.normalize("NFD", token)
        if unicodedata.category(c) != "Mn"
    )
    return _SPELLED_NUMBERS.get(token) or _SPELLED_NUMBERS.get(norm)


def _quantity_in_message(message: str) -> Optional[int]:
    """Quantidade de potes explicita na mensagem, ou None se ausente (story-041/046).

    So reconhece quando o numero vem colado a um substantivo de unidade ("3 potes")
    ou a um verbo de compra ("quero 2", "vou levar dois"). Numeros de outros campos
    (CEP, numero da casa, CPF) ou de duracao/parcelamento ("faz 2 anos", "em 2 vezes")
    NAO viram quantidade — protege o caminho de receita (guard story-046).
    """
    text = message or ""
    m = _QTY_UNIT_RE.search(text)
    if m:
        return _coerce_qty_token(m.group(1))
    m = _QTY_BUY_RE.search(text)
    if m:
        return _coerce_qty_token(m.group(1))
    return None


def _read_quantity_answer(message: str) -> Optional[int]:
    """Le a resposta a pergunta de quantidade (story-046), mais permissiva que
    `_quantity_in_message` porque o contexto JA e 'quantos potes?'. Aceita numero
    solto ("2", "dois"), o ciclo completo ("completo", "os dois") e a opcao de
    comecar com 1. Retorna None se nada confiavel for lido (cai para o LLM)."""
    text = (message or "").strip().lower()
    q = _quantity_in_message(text)
    if q is not None:
        return q
    if re.search(r"\b(complet[oa]|ciclo|os\s+dois|as\s+duas|ambos)\b", text):
        return 2
    m = _NUM_TOKEN_RE.search(text)
    if m:
        return _coerce_qty_token(m.group(1))
    return None


def _extract_quantity(message: str) -> int:
    """Extrai a quantidade de potes/unidades da mensagem-gatilho. Default 1."""
    return _quantity_in_message(message) or 1


def _detect_billing_type(message: str) -> Optional[str]:
    """Meio de pagamento explicito na mensagem, ou None se ausente (story-042).

    "cartao"/"credito"/"parcelar" -> CREDIT_CARD; "pix" -> PIX. Distingue
    "nao mencionou" (None, mantem o default/lembrado) de uma escolha explicita,
    espelhando `_quantity_in_message`. Cartao tem prioridade quando ambos
    aparecem (ex.: "nao quero pix, prefiro cartao").
    """
    text = message or ""
    if _CARD_RE.search(text):
        return "CREDIT_CARD"
    if _PIX_RE.search(text):
        return "PIX"
    return None


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


class InventoryReservationSweepScheduler:
    """APScheduler interval job that releases expired inventory reservations."""

    def __init__(self, tenant_id: str, interval_minutes: int = 5):
        self._tenant_id = tenant_id
        self._interval_minutes = interval_minutes
        self._scheduler: Any | None = None

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._release_expired,
            trigger="interval",
            minutes=self._interval_minutes,
            id=f"inventory_release_expired_{self._tenant_id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info(
            "InventoryReservationSweepScheduler started",
            extra={"tenant_id": self._tenant_id, "interval_minutes": self._interval_minutes},
        )

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _release_expired(self) -> int:
        from zwaf.memory.inventory_store import release_expired

        released = await release_expired(tenant_id=self._tenant_id)
        logger.info(
            "Inventory expired reservations released",
            extra={"tenant_id": self._tenant_id, "released": released},
        )
        return released


class LeadMemoryRetentionScheduler:
    """APScheduler job que purga memória de lead vencida (story-044, LGPD).

    Roda 1x/dia chamando `lead_store.purge_expired_memory`. Idempotente e sem PII
    em log. Compliance: roda independente da feature flag (não há memória a purgar
    enquanto a flag esteve sempre desligada — no-op).
    """

    def __init__(self, tenant_id: str, retention_months: int = 24, interval_minutes: int = 1440):
        self._tenant_id = tenant_id
        self._retention_months = retention_months
        self._interval_minutes = interval_minutes
        self._scheduler: Any | None = None

    def start(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._purge,
            trigger="interval",
            minutes=self._interval_minutes,
            id=f"lead_memory_retention_{self._tenant_id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info(
            "LeadMemoryRetentionScheduler started",
            extra={"tenant_id": self._tenant_id, "retention_months": self._retention_months},
        )

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def _purge(self) -> int:
        from zwaf.memory.lead_store import purge_expired_memory

        purged = await purge_expired_memory(
            tenant_id=self._tenant_id, retention_months=self._retention_months
        )
        logger.info(
            "Lead memory retention purge",
            extra={"tenant_id": self._tenant_id, "purged": purged},
        )
        return purged


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

    inventory_sweep_scheduler = InventoryReservationSweepScheduler(
        tenant_id=tenant_config.tenant_id,
        interval_minutes=5,
    )
    inventory_sweep_scheduler.start()

    # Story-044: purga de memória de lead vencida (LGPD). Retenção herda
    # lgpd.data_retention_days (ex.: 730 = 24 meses) ou cai em 24 meses.
    try:
        retention_days = int((tenant_config.lgpd or {}).get("data_retention_days", 0) or 0)
    except (TypeError, ValueError):
        retention_days = 0
    retention_months = max(1, retention_days // 30) if retention_days else 24
    lead_memory_retention_scheduler = LeadMemoryRetentionScheduler(
        tenant_id=tenant_config.tenant_id,
        retention_months=retention_months,
    )
    lead_memory_retention_scheduler.start()

    team = ZWAFTeam(
        tenant_config=tenant_config,
        whatsapp_tool=whatsapp,
        router=router,
        db_url=db_url,
        fidelizacao_scheduler=fidelizacao_scheduler,
        inventory_sweep_scheduler=inventory_sweep_scheduler,
        lead_memory_retention_scheduler=lead_memory_retention_scheduler,
    )
    return team
