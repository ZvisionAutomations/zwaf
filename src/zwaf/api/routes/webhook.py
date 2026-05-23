"""
Webhook endpoint — POST /v1/webhook/{tenant_id}

Recebe eventos da Evolution API e despacha para o ZWAFTeam do tenant correto.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("zwaf.api.webhook")

router = APIRouter()


# ─── Schemas Evolution API ────────────────────────────────────

class EvolutionMessageData(BaseModel):
    key: dict[str, Any] = {}
    message: dict[str, Any] = {}
    pushName: Optional[str] = None


class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict[str, Any] = {}


def _extract_message(payload: dict) -> tuple[str, str, str]:
    """
    Extrai (phone, text, push_name) do payload da Evolution API.
    Retorna ("", "", "") se a mensagem não for de texto.
    """
    data = payload.get("data", {})
    key = data.get("key", {})

    # Ignorar mensagens do próprio bot
    if key.get("fromMe", False):
        return "", "", ""

    phone = key.get("remoteJid", "").split("@")[0]
    push_name = data.get("pushName", phone)

    message = data.get("message", {})

    # Texto direto
    text = message.get("conversation", "")

    # Texto em mensagem extendida
    if not text:
        ext = message.get("extendedTextMessage", {})
        text = ext.get("text", "")

    return phone, text, push_name


@router.post("/{tenant_id}")
async def receive_webhook(
    tenant_id: str,
    request: Request,
) -> dict:
    """
    Recebe evento da Evolution API para um tenant específico.

    Eventos tratados: messages.upsert (mensagem recebida)
    Outros eventos: retornados com status "ignored"
    """
    # Ler payload raw
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = body.get("event", "")

    # Só processa mensagens recebidas
    if event != "messages.upsert":
        return {"status": "ignored", "event": event}

    phone, text, push_name = _extract_message(body)

    if not phone or not text:
        return {"status": "ignored", "reason": "no_text_content"}

    # Buscar o team do tenant no estado da app
    teams = request.app.state.teams
    if tenant_id not in teams:
        logger.warning("Unknown tenant_id in webhook: %s", tenant_id)
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    team = teams[tenant_id]
    session_id = f"{tenant_id}_{phone}"
    lead_id = phone

    logger.info(
        "Webhook message received",
        extra={
            "tenant_id": tenant_id,
            "phone_tail": phone[-4:],
            "text_preview": text[:50],
        },
    )

    # Processar via ZWAFTeam (assíncrono — não bloqueia o webhook)
    import asyncio
    asyncio.create_task(
        _process_and_respond(team, text, phone, session_id, lead_id, tenant_id)
    )

    return {"status": "accepted"}


async def _process_and_respond(team, message, phone, session_id, lead_id, tenant_id):
    """Processa a mensagem e envia a resposta via WhatsApp."""
    try:
        response = await team.process(
            message=message,
            phone=phone,
            session_id=session_id,
            lead_id=lead_id,
        )
        logger.info(
            "Response generated",
            extra={
                "tenant_id": tenant_id,
                "agent_used": response.agent_used,
                "latency_ms": round(response.latency_ms),
            },
        )
        await team.send_response(phone=phone, text=response.response, session_id=session_id)
    except Exception as e:
        logger.error(
            "Failed to process webhook message",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
