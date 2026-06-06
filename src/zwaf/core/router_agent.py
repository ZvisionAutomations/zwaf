"""
RouterAgent — Roteador híbrido keywords→LLM para o ZWAF.

Prioridade:
1. Keyword exata (case-insensitive substring) → route direto, latência ~0ms
2. Sem keyword → LLM classifica intent, latência ~500ms
3. LLM falha ou fallback_llm=False → Vendedor (default)

FidelizacaoAgent NUNCA é roteado por mensagem — só por evento agendado.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("zwaf.core.router")

# Ordem de prioridade quando dois intents colidem (maior = mais prioritário)
_INTENT_PRIORITY = {
    "cobranca": 4,
    "suporte": 3,
    "recompra": 2,
    "vendedor": 1,
    "fidelizacao": -1,  # nunca roteado por mensagem
}

_FORBIDDEN_AGENTS = {"fidelizacao"}


@dataclass
class RouteResult:
    agent_name: str
    confidence: float
    via_llm: bool = False


class RouterAgent:
    """
    Roteador híbrido:
    - Fase 1: keyword match (case-insensitive, substring)
    - Fase 2: LLM fallback (quando fallback_llm=True e sem keyword match)
    - Default: vendedor

    purchase_history_fn(phone: str) -> bool — opcional para o edge case "oi" de recompra.
    """

    def __init__(
        self,
        config,  # RouterConfig — import evitado para não criar ciclo
        purchase_history_fn: Optional[Callable[[str], bool]] = None,
        llm_model=None,  # Agno model — injetado externamente
    ):
        self._config = config
        self._purchase_history_fn = purchase_history_fn or (lambda _: False)
        self._llm_model = llm_model

    async def route(self, message: str, phone: str = "") -> RouteResult:
        """
        Classifica a mensagem e retorna o agente responsável.

        Edge cases:
        - Mensagem vazia ou só emoji → Vendedor
        - "oi" sem histórico → Vendedor; com histórico → Recompra
        - Dois intents → prioridade: Cobrança > Suporte > Recompra > Vendedor
        - FidelizacaoAgent → nunca retornado aqui
        """
        stripped = message.strip()

        # Vazio, só emoji ou cumprimento curto
        if not stripped or _is_greeting_only(stripped):
            if self._purchase_history_fn(phone):
                return await self._fallback_with_history(stripped, phone)
            return RouteResult(agent_name="vendedor", confidence=0.95)

        if _is_existing_payment_problem(stripped):
            return RouteResult(agent_name="cobranca", confidence=0.95, via_llm=False)

        if _is_new_checkout_payment_intent(stripped):
            return RouteResult(agent_name="vendedor", confidence=0.95, via_llm=False)

        # Keyword match
        keyword_result = self._keyword_match(stripped)
        if keyword_result is not None:
            return keyword_result

        # LLM fallback
        if self._config.fallback_llm:
            try:
                return await self._llm_classify(stripped, phone)
            except Exception as e:
                logger.warning(
                    "LLM classification failed — defaulting to vendedor",
                    extra={"error": str(e), "message_preview": stripped[:50]},
                )

        return RouteResult(agent_name="vendedor", confidence=0.3)

    def _keyword_match(self, message: str) -> Optional[RouteResult]:
        """
        Verifica keywords configuradas no TenantConfig.router.keywords.
        Case-insensitive substring match.
        Retorna o intent de maior prioridade se múltiplos baterem.
        Nunca retorna agentes em _FORBIDDEN_AGENTS.
        """
        msg_lower = message.lower()
        matched: list[tuple[int, str]] = []  # (priority, agent_name)

        for agent_name, keywords in self._config.keywords.items():
            if agent_name in _FORBIDDEN_AGENTS:
                continue
            for kw in keywords:
                if kw.lower() in msg_lower:
                    priority = _INTENT_PRIORITY.get(agent_name, 0)
                    matched.append((priority, agent_name))
                    break  # um keyword basta por agente

        if not matched:
            return None

        # Maior prioridade vence
        matched.sort(key=lambda x: x[0], reverse=True)
        best_priority, best_agent = matched[0]
        return RouteResult(agent_name=best_agent, confidence=0.95, via_llm=False)

    async def _llm_classify(self, message: str, phone: str) -> RouteResult:
        """
        Classifica intent via LLM quando keywords não batem.
        Retorna RouteResult com via_llm=True.

        Implementação real injetada via self._llm_model.
        Esta implementação de fallback é usada quando llm_model=None.
        """
        if self._llm_model is None:
            # Sem modelo injetado → default vendedor
            return RouteResult(agent_name="vendedor", confidence=0.3, via_llm=True)

        agents = [a for a in self._config.keywords.keys() if a not in _FORBIDDEN_AGENTS]
        agents_list = ", ".join(agents)

        prompt = (
            f"Classifique a seguinte mensagem de WhatsApp em um dos agentes: {agents_list}.\n"
            f"Responda APENAS com o nome do agente (sem explicação).\n\n"
            f"Mensagem: {message}"
        )

        try:
            response = await self._llm_model.arun(prompt)
            agent_name = response.content.strip().lower()

            # Valida que o LLM retornou um agente válido
            if agent_name in agents and agent_name not in _FORBIDDEN_AGENTS:
                return RouteResult(agent_name=agent_name, confidence=0.75, via_llm=True)
        except Exception:
            raise

        # LLM retornou algo inválido → default
        return RouteResult(agent_name="vendedor", confidence=0.3, via_llm=True)

    async def _fallback_with_history(self, message: str, phone: str) -> RouteResult:
        """Lead com histórico de compra enviando saudação → Recompra via LLM."""
        if self._config.fallback_llm:
            try:
                return await self._llm_classify(message or "oi", phone)
            except Exception:
                pass
        return RouteResult(agent_name="recompra", confidence=0.7)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

_GREETING_WORDS = {"oi", "olá", "ola", "hey", "hi", "bom dia", "boa tarde", "boa noite", "e aí", "eai"}


_EXISTING_PAYMENT_PROBLEM_TERMS = (
    "nao consegui pagar",
    "não consegui pagar",
    "nao consigo pagar",
    "não consigo pagar",
    "link expirou",
    "link vencido",
    "erro no pagamento",
    "problema com pagamento",
    "pagamento deu erro",
    "pix expirou",
)

_NEW_CHECKOUT_PAYMENT_TERMS = (
    "gerar link",
    "gera o link",
    "gerar o link",
    "gerar um link",
    "mandar link",
    "manda o link",
    "enviar link",
    "envia o link",
    "link de pagamento",
    "quero pagar",
    "pagar via pix",
    "pagar por pix",
    "pix",
    "comprar",
    "quero comprar",
    "fechar pedido",
    "finalizar pedido",
)


def _is_existing_payment_problem(message: str) -> bool:
    msg_lower = message.lower()
    return any(term in msg_lower for term in _EXISTING_PAYMENT_PROBLEM_TERMS)


def _is_new_checkout_payment_intent(message: str) -> bool:
    msg_lower = message.lower()
    return any(term in msg_lower for term in _NEW_CHECKOUT_PAYMENT_TERMS)


def _is_greeting_only(message: str) -> bool:
    """
    Retorna True se a mensagem é apenas uma saudação ou emoji.
    Considera mensagens curtas (até 20 chars) que sejam só saudação/emoji.
    """
    if len(message) > 40:
        return False
    msg_lower = message.lower().strip("!?. 👋🌿😊💚")
    return msg_lower in _GREETING_WORDS or _is_only_emoji(message)


def _is_only_emoji(text: str) -> bool:
    """Retorna True se o texto contém apenas emojis/espaços (sem letras ou dígitos)."""
    for char in text:
        if char.isalpha() or char.isdigit():
            return False
    return True
