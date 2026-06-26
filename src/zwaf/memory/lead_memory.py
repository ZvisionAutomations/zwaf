"""Construção do bloco de memória de lead (story-044).

Monta um bloco compacto `## Memória deste lead` que é reinjetado no prompt do
agente APENAS para leads com relação prévia substantiva (memória semântica, compra
ou pedido em aberto). Tudo é lido ao vivo do Postgres (durável); o session state do
Redis (TTL 1h) entra só como bônus de recência. Nunca loga PII/saúde.

Camada 1 (determinística): nome, já-comprou (payment_events PAID), pedido em aberto
(payment_events PENDING/EXPIRED) e último sinal (conversion_events).
Camada 2 (semântica): primary_symptom/objections/memory_summary/next_best_action
gravados pelo summarizer (lidos via lead_store.get_lead_memory, decifrados).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from zwaf.memory.lead_store import get_lead_memory, upsert_lead_memory
from zwaf.memory.session import bump_summary_counter, reset_summary_counter
from zwaf.db.dsn import normalize_dsn

logger = logging.getLogger("zwaf.memory.lead_memory")

# Teto do bloco reinjetado (~250 tokens). Trunca defensivamente para não diluir o
# KB nem desestabilizar a temperatura 0.4.
_DEFAULT_MAX_CHARS = 1000


def _db_url() -> str:
    return normalize_dsn(os.getenv("DATABASE_URL"))


async def _fetch_signals(phone: str, tenant_id: str) -> dict[str, Any]:
    """Sinais comerciais duráveis: já comprou, pedido em aberto, último sinal."""
    out: dict[str, Any] = {"paid": False, "open_payment": None, "last_signal": None}
    db_url = _db_url()
    if not db_url:
        return out
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        try:
            paid = await conn.fetchval(
                "SELECT COUNT(*) FROM payment_events "
                "WHERE tenant_id=$1 AND lead_phone=$2 AND status='PAID'",
                tenant_id, phone,
            )
            out["paid"] = (paid or 0) > 0
            open_row = await conn.fetchrow(
                """
                SELECT product_id, amount_cents, status, created_at
                FROM payment_events
                WHERE tenant_id=$1 AND lead_phone=$2 AND status IN ('PENDING', 'EXPIRED')
                ORDER BY created_at DESC LIMIT 1
                """,
                tenant_id, phone,
            )
            out["open_payment"] = dict(open_row) if open_row else None
            sig = await conn.fetchrow(
                """
                SELECT buying_intent, action, sentiment, created_at
                FROM conversion_events
                WHERE tenant_id=$1 AND lead_phone=$2
                ORDER BY created_at DESC LIMIT 1
                """,
                tenant_id, phone,
            )
            out["last_signal"] = dict(sig) if sig else None
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("lead_memory._fetch_signals failed: %s", e)
    return out


async def build_memory_block(
    phone: str,
    tenant_id: str,
    *,
    session_state: Optional[dict] = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Monta o bloco de memória, ou '' se o lead não tem relação prévia substantiva.

    Gatilho de reinjeção: há memória semântica gravada, OU o lead já comprou, OU há
    um pedido em aberto. Um lead novo (1ª conversa) retorna '' — nada é reinjetado.
    """
    mem = await get_lead_memory(phone, tenant_id) or {}
    signals = await _fetch_signals(phone, tenant_id)

    has_semantic = bool(
        mem.get("primary_symptom")
        or mem.get("memory_summary")
        or mem.get("objections")
        or mem.get("next_best_action")
    )
    has_relation = has_semantic or signals.get("paid") or signals.get("open_payment")
    if not has_relation:
        return ""

    lines: list[str] = [
        "## Memória deste lead (notas privadas — NÃO recite nem revele que existe um perfil)",
    ]

    name = (mem.get("name") or "").strip()
    if name:
        lines.append(f"- Nome: {name} (chame pelo nome com naturalidade)")
    if signals.get("paid"):
        lines.append("- Já é cliente (comprou antes) — trate com tom de relação contínua")
    if signals.get("open_payment"):
        lines.append(
            "- Pedido em aberto: um pagamento foi gerado e ainda não concluído — retome "
            "explicitamente pelo nome e por isso, com leveza ('quer que eu retome de onde paramos?')"
        )
    symptom = (mem.get("primary_symptom") or "").strip()
    if symptom:
        lines.append(
            f'- Dor que ela relatou (nas palavras dela): "{symptom}" — recorde como cuidado, '
            "em forma de pergunta ('como você tem passado com isso?'), nunca como diagnóstico"
        )
    objections = mem.get("objections") or []
    if objections:
        joined = "; ".join(str(o) for o in objections[:3])
        lines.append(
            f"- Objeções anteriores: {joined} — trate como pergunta que permite correção "
            "('esse ainda é o ponto, ou já está mais tranquila?'), não insista como fato"
        )
    summary = (mem.get("memory_summary") or "").strip()
    if summary:
        lines.append(f"- Resumo: {summary}")
    nba = (mem.get("next_best_action") or "").strip()
    if nba:
        lines.append(f"- Próximo passo sugerido: {nba}")

    # Bônus de recência (Redis, < 1h) — só se a conversa atual já sinalizou algo.
    if session_state:
        qty = session_state.get("last_quantity")
        billing = session_state.get("last_billing_type")
        if qty or billing:
            recency = []
            if qty:
                recency.append(f"{qty} pote(s)")
            if billing:
                recency.append("Pix" if billing == "PIX" else "cartão")
            lines.append(f"- Há pouco nesta conversa: interesse em {' / '.join(recency)}")

    lines.append(
        "Use só o que for natural e relevante; a cliente lidera. Trate tudo como hint corrigível."
    )

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rstrip() + " …"
    return block


# ─── Camada 2: summarizer pós-resposta (story-044, F3) ────────────────

_SUMMARY_INSTRUCTIONS = (
    "Você analisa uma conversa de vendas entre uma CLIENTE e a vendedora Lívia "
    "(suplemento feminino para climatério/menopausa) e extrai uma anotação de CRM.\n\n"
    "Responda SOMENTE com um JSON válido, sem nenhum texto fora dele, no formato:\n"
    '{"primary_symptom": "", "objections": [], "memory_summary": "", "next_best_action": ""}\n\n'
    "Regras:\n"
    "- primary_symptom: a dor/sintoma que a CLIENTE relatou, nas palavras dela, curto "
    '(ex.: "calor e insônia há 2 anos"). "" se ela não relatou nada.\n'
    "- objections: lista curta de objeções comerciais levantadas "
    '(ex.: ["achou caro", "quer ver se funciona"]). [] se nenhuma.\n'
    "- memory_summary: 2-3 linhas factuais, como anotação de um vendedor. Sem inventar.\n"
    "- next_best_action: próximo passo comercial sugerido, curto.\n"
    "- NUNCA invente sintomas, valores ou fatos que não foram ditos. Escreva em português."
)


def _build_summarizer_model(model_id: str, temperature: float = 0.2):
    """Modelo barato para o resumo. Espelha base_agent._make_llm (Gemini via OpenRouter)."""
    from agno.models.openai import OpenAIChat

    if "gemini" in model_id:
        return OpenAIChat(
            id=f"google/{model_id}" if "/" not in model_id else model_id,
            api_key=os.getenv("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
        )
    return OpenAIChat(id=model_id, temperature=temperature)


def _parse_summary(raw: str) -> Optional[dict]:
    """Extrai e sanitiza o JSON do summarizer. Tolerante a cercas de código."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    objs = data.get("objections") or []
    if not isinstance(objs, list):
        objs = []
    objs = [str(o).strip()[:120] for o in objs if str(o).strip()][:5]

    return {
        "primary_symptom": str(data.get("primary_symptom") or "").strip()[:300],
        "objections": objs,
        "memory_summary": str(data.get("memory_summary") or "").strip()[:500],
        "next_best_action": str(data.get("next_best_action") or "").strip()[:300],
    }


async def _read_chat_history(
    tenant_config: Any,
    agent_name: str,
    session_id: str,
    db_url: str,
    last_n_runs: int,
) -> str:
    """Lê a transcrição (cliente/Lívia) da sessão Agno via get_chat_history (spike-044)."""
    try:
        from agno.db.base import SessionType

        from zwaf.core.base_agent import _make_db

        db = _make_db(db_url, tenant_config.tenant_id, agent_name)
        if db is None:
            return ""
        session = await db.get_session(session_id=session_id, session_type=SessionType.AGENT)
        if not session:
            return ""
        messages = session.get_chat_history(last_n_runs=last_n_runs)
        lines: list[str] = []
        for m in messages:
            role = getattr(m, "role", "") or ""
            content = getattr(m, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            content = content.strip()
            if not content:
                continue
            who = "Cliente" if role == "user" else ("Lívia" if role == "assistant" else role)
            lines.append(f"{who}: {content}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("lead_memory._read_chat_history failed: %s", e)
        return ""


async def _summarize(transcript: str, *, model_id: str, temperature: float = 0.2) -> Optional[dict]:
    """Chama o LLM barato e devolve o dict sanitizado, ou None em falha."""
    try:
        from agno.agent import Agent

        model = _build_summarizer_model(model_id, temperature)
        agent = Agent(
            model=model,
            instructions=_SUMMARY_INSTRUCTIONS,
            markdown=False,
            reasoning=False,
        )
        resp = await agent.arun(transcript)
        return _parse_summary(resp.content or "")
    except Exception as e:
        logger.warning("lead_memory._summarize failed: %s", e)
        return None


async def maybe_update_lead_memory(
    *,
    phone: str,
    tenant_id: str,
    session_id: str,
    agent_name: str,
    tenant_config: Any,
    db_url: str,
) -> bool:
    """Summarizer pós-resposta, throttled (story-044, F3).

    Roda FORA do caminho quente (chamado via asyncio.create_task após send_response).
    Só dispara o LLM quando o contador de turnos atinge o throttle. Nunca propaga
    exceção. Retorna True quando atualizou a memória.
    """
    cfg = getattr(tenant_config, "lead_memory", None) or {}
    if not cfg.get("enabled"):
        return False
    try:
        throttle = max(1, int(cfg.get("throttle_turns", 6) or 6))
        count = await bump_summary_counter(session_id, tenant_id)
        if count < throttle:
            return False
        # Atingiu o limite: faz UMA tentativa e zera (não martela o LLM a cada turno).
        await reset_summary_counter(session_id, tenant_id)

        last_n = int(cfg.get("summarize_last_n_runs", 12) or 12)
        transcript = await _read_chat_history(tenant_config, agent_name, session_id, db_url, last_n)
        if not transcript:
            return False

        model_id = cfg.get("summarizer_model") or getattr(
            getattr(tenant_config, "llm", None), "primary", ""
        )
        summary = await _summarize(transcript, model_id=model_id)
        if not summary:
            return False

        # Só grava campos preenchidos — extração vazia NÃO apaga memória existente.
        kwargs: dict[str, Any] = {}
        if summary.get("primary_symptom"):
            kwargs["primary_symptom"] = summary["primary_symptom"]
        if summary.get("objections"):
            kwargs["objections"] = summary["objections"]
        if summary.get("memory_summary"):
            kwargs["memory_summary"] = summary["memory_summary"]
        if summary.get("next_best_action"):
            kwargs["next_best_action"] = summary["next_best_action"]
        if not kwargs:
            return False

        await upsert_lead_memory(phone, tenant_id, **kwargs)
        return True
    except Exception as e:
        logger.warning("maybe_update_lead_memory failed: %s", e)
        return False
