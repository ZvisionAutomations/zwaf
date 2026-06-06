"""TDD — testes para RouterAgent (20 cenários)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zwaf.core.router_agent import RouteResult, RouterAgent
from zwaf.core.tenant import RouterConfig


KEYWORDS = {
    "vendedor": [
        "quero comprar",
        "quanto custa",
        "como funciona",
        "tem desconto",
        "valor",
        "pix",
        "pagar via pix",
        "pagar por pix",
        "quero pagar",
        "gerar link",
        "gerar o link",
        "mandar link",
        "manda o link",
        "enviar link",
        "link de pagamento",
        "fechar pedido",
        "finalizar pedido",
    ],
    "recompra": ["quero pedir de novo", "acabou", "renovar", "segundo pote"],
    "suporte": ["não chegou", "problema", "dúvida", "como tomar", "efeito"],
    "cobranca": ["não consegui pagar", "nao consegui pagar", "link expirou", "erro no pagamento", "problema com pagamento"],
}

ROUTER_CONFIG = RouterConfig(keywords=KEYWORDS, fallback_llm=True)


def make_router(has_purchase_history: bool = False) -> RouterAgent:
    return RouterAgent(
        config=ROUTER_CONFIG,
        purchase_history_fn=lambda phone: has_purchase_history,
    )


# ─────────────────────────────────────────────────────────────
# Keyword matching (latência ~0ms — sem LLM)
# ─────────────────────────────────────────────────────────────

class TestKeywordRouting:
    @pytest.mark.asyncio
    async def test_quero_comprar_routes_to_vendedor(self):
        router = make_router()
        result = await router.route("quero comprar agora", phone="5511999990001")
        assert result.agent_name == "vendedor"
        assert result.confidence >= 0.9
        assert result.via_llm is False

    @pytest.mark.asyncio
    async def test_quanto_custa_routes_to_vendedor(self):
        router = make_router()
        result = await router.route("quanto custa o produto?", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_pix_routes_to_vendedor_for_new_checkout(self):
        router = make_router()
        result = await router.route("quero pagar via pix", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_payment_link_generation_routes_to_vendedor_without_llm(self):
        router = make_router()
        with patch.object(
            router,
            "_llm_classify",
            AsyncMock(return_value=RouteResult("suporte", 0.75, via_llm=True)),
        ) as mock_llm:
            result = await router.route(
                "nao consigo gerar link de pagamento",
                phone="5511999990001",
            )

        assert result.agent_name == "vendedor"
        assert result.via_llm is False
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_payment_problem_routes_to_cobranca(self):
        router = make_router()
        result = await router.route("nao consegui pagar o link", phone="5511999990001")
        assert result.agent_name == "cobranca"

    @pytest.mark.asyncio
    async def test_current_payment_failure_routes_to_cobranca_before_checkout_terms(self):
        router = make_router()
        result = await router.route(
            "nao consigo pagar o link de pagamento",
            phone="5511999990001",
        )
        assert result.agent_name == "cobranca"

    @pytest.mark.asyncio
    async def test_nao_chegou_routes_to_suporte(self):
        router = make_router()
        result = await router.route("meu pedido não chegou", phone="5511999990001")
        assert result.agent_name == "suporte"

    @pytest.mark.asyncio
    async def test_quero_pedir_de_novo_routes_to_recompra(self):
        router = make_router()
        result = await router.route("quero pedir de novo", phone="5511999990001")
        assert result.agent_name == "recompra"

    @pytest.mark.asyncio
    async def test_keyword_is_case_insensitive(self):
        router = make_router()
        result = await router.route("QUERO COMPRAR", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_keyword_is_substring_match(self):
        router = make_router()
        result = await router.route("eu quero comprar sim!", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_keyword_match_is_fast(self):
        """Keyword routing deve ser síncrono — sem chamada LLM."""
        router = make_router()
        with patch.object(router, "_llm_classify", AsyncMock()) as mock_llm:
            await router.route("quero comprar", phone="5511999990001")
            mock_llm.assert_not_called()


# ─────────────────────────────────────────────────────────────
# Prioridade de intent duplo
# ─────────────────────────────────────────────────────────────

class TestDoubleIntentPriority:
    @pytest.mark.asyncio
    async def test_cobranca_beats_suporte(self):
        """Cobrança > Suporte quando ambos os keywords presentes."""
        router = make_router()
        result = await router.route("tenho problema com pagamento e o link expirou", phone="5511999990001")
        assert result.agent_name == "cobranca"

    @pytest.mark.asyncio
    async def test_suporte_beats_recompra(self):
        """Suporte > Recompra quando ambos presentes."""
        router = make_router()
        result = await router.route("acabou meu pote mas tive problema", phone="5511999990001")
        assert result.agent_name == "suporte"

    @pytest.mark.asyncio
    async def test_recompra_beats_vendedor(self):
        """Recompra > Vendedor quando ambos presentes."""
        router = make_router()
        result = await router.route("quero pedir de novo, quanto custa", phone="5511999990001")
        assert result.agent_name == "recompra"


# ─────────────────────────────────────────────────────────────
# Edge cases — SPEC seção 7
# ─────────────────────────────────────────────────────────────

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_message_routes_to_vendedor(self):
        router = make_router()
        result = await router.route("", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_only_emoji_routes_to_vendedor(self):
        router = make_router()
        result = await router.route("😊🌿", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_oi_routes_to_vendedor_no_history(self):
        """Lead sem histórico enviando 'oi' → Vendedor (primeiro contato)."""
        router = make_router(has_purchase_history=False)
        result = await router.route("oi", phone="5511999990001")
        assert result.agent_name == "vendedor"

    @pytest.mark.asyncio
    async def test_oi_routes_to_recompra_with_history(self):
        """Lead que já comprou enviando 'oi' → Recompra."""
        router = make_router(has_purchase_history=True)
        with patch.object(router, "_llm_classify", AsyncMock(return_value=RouteResult("recompra", 0.7, via_llm=True))):
            result = await router.route("oi", phone="5511999990001")
            assert result.agent_name == "recompra"

    @pytest.mark.asyncio
    async def test_fidelizacao_never_routed_by_message(self):
        """FidelizacaoAgent nunca deve ser retornado por mensagem — só por evento agendado."""
        keywords_with_fidelizacao = {
            **KEYWORDS,
            "fidelizacao": ["obrigada", "adorei", "voltarei"],
        }
        config = RouterConfig(keywords=keywords_with_fidelizacao, fallback_llm=False)
        router = RouterAgent(config=config)
        result = await router.route("adorei o produto, obrigada", phone="5511999990001")
        assert result.agent_name != "fidelizacao"

    @pytest.mark.asyncio
    async def test_default_fallback_is_vendedor(self):
        """Quando LLM não classifica, o padrão é Vendedor."""
        router_no_llm = RouterAgent(
            config=RouterConfig(keywords=KEYWORDS, fallback_llm=False)
        )
        result = await router_no_llm.route("blablabla xyz", phone="5511999990001")
        assert result.agent_name == "vendedor"


# ─────────────────────────────────────────────────────────────
# LLM fallback
# ─────────────────────────────────────────────────────────────

class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_llm_called_when_no_keyword_match(self):
        router = make_router()
        with patch.object(
            router, "_llm_classify",
            AsyncMock(return_value=RouteResult("suporte", 0.75, via_llm=True))
        ) as mock_llm:
            result = await router.route("preciso de ajuda com algo", phone="5511999990001")
            mock_llm.assert_called_once()
            assert result.agent_name == "suporte"
            assert result.via_llm is True

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_vendedor(self):
        router = make_router()
        with patch.object(router, "_llm_classify", AsyncMock(side_effect=Exception("LLM timeout"))):
            result = await router.route("mensagem ambígua", phone="5511999990001")
            assert result.agent_name == "vendedor"
            assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_llm_returns_result_object(self):
        router = make_router()
        with patch.object(
            router, "_llm_classify",
            AsyncMock(return_value=RouteResult("recompra", 0.82, via_llm=True))
        ):
            result = await router.route("mensagem sem keyword", phone="5511999990001")
            assert isinstance(result, RouteResult)
            assert result.confidence == 0.82
