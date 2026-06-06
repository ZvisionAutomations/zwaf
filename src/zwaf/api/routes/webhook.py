"""
Webhook endpoint - POST /v1/webhook/{tenant_id}

Recebe eventos da Evolution API e despacha para o ZWAFTeam do tenant correto.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("zwaf.api.webhook")

router = APIRouter()


class EvolutionMessageData(BaseModel):
    key: dict[str, Any] = Field(default_factory=dict)
    message: dict[str, Any] = Field(default_factory=dict)
    pushName: Optional[str] = None


class EvolutionWebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict[str, Any] = Field(default_factory=dict)


def _extract_message(payload: dict) -> tuple[str, str, str]:
    """
    Extrai (phone, text, push_name) do payload da Evolution API.
    Retorna ("", "", "") se a mensagem nao for de texto.
    """
    data = payload.get("data", {})
    key = data.get("key", {})

    if key.get("fromMe", False):
        return "", "", ""

    phone = key.get("remoteJid", "").split("@")[0]
    push_name = data.get("pushName", phone)

    message = data.get("message", {})
    text = message.get("conversation", "")

    if not text:
        ext = message.get("extendedTextMessage", {})
        text = ext.get("text", "")

    return phone, text, push_name


def _extract_audio_message(payload: dict) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """Extract (phone, message, key, push_name) when payload contains supported audio."""
    data = payload.get("data", {})
    key = data.get("key", {})
    if key.get("fromMe", False):
        return "", {}, {}, ""

    phone = key.get("remoteJid", "").split("@")[0]
    push_name = data.get("pushName", phone)
    message = data.get("message", {})
    try:
        from zwaf.audio.transcription import has_audio_message

        if phone and has_audio_message(message):
            return phone, message, key, push_name
    except Exception:
        return "", {}, {}, ""
    return "", {}, {}, ""


def _expected_instances(team: Any) -> set[str]:
    tenant_config = getattr(team, "_tenant", None)
    whatsapp = getattr(tenant_config, "whatsapp", None)
    phone_numbers = getattr(whatsapp, "phone_numbers", []) or []
    return {entry.instance for entry in phone_numbers if getattr(entry, "instance", "")}


@router.post("/{tenant_id}")
async def receive_webhook(
    tenant_id: str,
    request: Request,
) -> dict:
    """
    Recebe evento da Evolution API para um tenant especifico.

    Eventos tratados: messages.upsert.
    Outros eventos: retornados com status "ignored".
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    teams = getattr(request.app.state, "teams", {})
    if tenant_id not in teams:
        logger.warning("Unknown tenant_id in webhook: %s", tenant_id)
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    try:
        payload = EvolutionWebhookPayload.model_validate(body)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Malformed Evolution payload")

    team = teams[tenant_id]
    expected_instances = _expected_instances(team)
    if expected_instances and payload.instance not in expected_instances:
        logger.warning(
            "Evolution webhook rejected invalid instance",
            extra={"tenant_id": tenant_id, "instance": payload.instance},
        )
        raise HTTPException(status_code=403, detail="Invalid Evolution instance")

    if payload.event != "messages.upsert":
        return {"status": "ignored", "event": payload.event}

    phone, text, _push_name = _extract_message(body)

    if phone and text:
        session_id = f"{tenant_id}_{phone}"
        lead_id = phone

        logger.info(
            "Webhook message received",
            extra={
                "tenant_id": tenant_id,
                "phone_tail": phone[-4:],
                "text_length": len(text),
                "instance": payload.instance,
            },
        )

        asyncio.create_task(
            _process_and_respond(team, text, phone, session_id, lead_id, tenant_id)
        )

        return {"status": "accepted"}

    audio_phone, audio_message, message_key, _audio_push_name = _extract_audio_message(body)
    if audio_phone and audio_message:
        session_id = f"{tenant_id}_{audio_phone}"
        lead_id = audio_phone
        logger.info(
            "Webhook audio message received",
            extra={
                "tenant_id": tenant_id,
                "phone_tail": audio_phone[-4:],
                "instance": payload.instance,
            },
        )
        await _process_audio_and_respond(
            team=team,
            audio_message=audio_message,
            message_key=message_key,
            phone=audio_phone,
            session_id=session_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            instance=payload.instance,
        )
        return {"status": "accepted"}

    return {"status": "ignored", "reason": "no_text_content"}


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
        _record_observability(
            tenant_id=tenant_id,
            phone=phone,
            agent_used=response.agent_used,
            latency_ms=response.latency_ms,
            status="ok",
        )
        await team.send_response(phone=phone, text=response.response, session_id=session_id)
    except Exception as e:
        logger.error(
            "Failed to process webhook message",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        _record_observability(
            tenant_id=tenant_id,
            phone=phone,
            agent_used="error",
            latency_ms=0.0,
            status="error",
            error=str(e),
        )


async def _process_audio_and_respond(
    *,
    team,
    audio_message: dict[str, Any],
    message_key: dict[str, Any],
    phone: str,
    session_id: str,
    lead_id: str,
    tenant_id: str,
    instance: str,
) -> None:
    """Transcribe supported audio and route the resulting text through the normal agent flow."""
    try:
        from zwaf.audio.transcription import (
            AudioContent,
            DEFAULT_FALLBACK_MESSAGE,
            TranscriptionResult,
            load_audio_content,
            transcribe_audio,
        )

        audio = await load_audio_content(
            message=audio_message,
            instance=instance,
            message_key=message_key,
            evolution_url=os.getenv("EVOLUTION_API_URL", ""),
            evolution_api_key=os.getenv("EVOLUTION_API_KEY", ""),
        )
        if isinstance(audio, TranscriptionResult):
            await _send_audio_fallback(team, phone, session_id, audio.fallback_message)
            _record_observability(
                tenant_id=tenant_id,
                phone=phone,
                agent_used="audio_transcription",
                latency_ms=0.0,
                status=audio.code,
            )
            return
        if not isinstance(audio, AudioContent):
            await _send_audio_fallback(team, phone, session_id, DEFAULT_FALLBACK_MESSAGE)
            return

        result = await transcribe_audio(audio)
        if not result.ok or not result.text.strip():
            await _send_audio_fallback(team, phone, session_id, result.fallback_message)
            _record_observability(
                tenant_id=tenant_id,
                phone=phone,
                agent_used="audio_transcription",
                latency_ms=0.0,
                status=result.code,
            )
            return

        await _process_and_respond(team, result.text.strip(), phone, session_id, lead_id, tenant_id)
    except Exception as exc:
        logger.error(
            "Failed to process audio webhook message",
            extra={"tenant_id": tenant_id, "error_type": type(exc).__name__},
        )
        await _send_audio_fallback(team, phone, session_id, DEFAULT_FALLBACK_MESSAGE)


async def _send_audio_fallback(team, phone: str, session_id: str, message: str) -> None:
    try:
        await team.send_response(phone=phone, text=message, session_id=session_id)
    except Exception:
        logger.warning("Audio transcription fallback delivery failed")


def _record_observability(
    *,
    tenant_id: str,
    phone: str,
    agent_used: str,
    latency_ms: float,
    status: str,
    error: str = "",
) -> None:
    """Best-effort Langfuse trace; never affects the customer flow."""
    try:
        from zwaf.observability import langfuse as obs

        obs.record_conversation(
            name="whatsapp-conversation",
            session_seed=f"{tenant_id}:{phone}",
            user_seed=phone,
            tags=[f"tenant:{tenant_id}", "feature:whatsapp-agent"],
            metadata={
                "tenant_id": tenant_id,
                "agent_used": agent_used,
                "feature": "whatsapp-agent",
                "phone_tail": obs.phone_tail(phone),
                "latency_ms": round(latency_ms),
                "status": status,
                "error": obs.mask_pii(error) if error else "",
            },
        )
    except Exception:
        pass
