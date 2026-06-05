"""Audio transcription providers for WhatsApp voice notes."""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("zwaf.audio.transcription")

DEFAULT_FALLBACK_MESSAGE = (
    "Recebi seu audio, mas nao consegui transcrever agora. "
    "Pode me mandar por texto?"
)
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
SUPPORTED_AUDIO_KEYS = ("audioMessage", "pttMessage")


@dataclass(frozen=True)
class AudioContent:
    bytes_data: bytes
    filename: str = "whatsapp-audio.ogg"
    content_type: str = "audio/ogg"
    message_id: str = ""
    duration_seconds: int | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    ok: bool
    text: str = ""
    code: str = "ok"
    fallback_message: str = DEFAULT_FALLBACK_MESSAGE


def transcription_enabled() -> bool:
    return _provider_name() != "disabled"


def _provider_name() -> str:
    return (os.getenv("TRANSCRIPTION_PROVIDER", "disabled") or "disabled").strip().lower()


def _fallback_provider_name() -> str:
    return (os.getenv("TRANSCRIPTION_FALLBACK_PROVIDER", "disabled") or "disabled").strip().lower()


def _max_audio_bytes() -> int:
    raw = os.getenv("TRANSCRIPTION_MAX_BYTES") or os.getenv("TRANSCRIPTION_MAX_AUDIO_BYTES") or "26214400"
    try:
        return max(1, int(raw))
    except ValueError:
        return 26214400


def _allowed_mime_types() -> set[str]:
    raw = os.getenv(
        "TRANSCRIPTION_ALLOWED_MIME_TYPES",
        "audio/ogg,audio/opus,audio/mpeg,audio/mp4,audio/webm,audio/wav",
    )
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _timeout_seconds() -> float:
    raw = os.getenv("TRANSCRIPTION_TIMEOUT_SECONDS", "20")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 20.0


def extract_audio_descriptor(message: dict[str, Any]) -> dict[str, Any]:
    """Return the first supported Evolution audio payload descriptor."""
    for key in SUPPORTED_AUDIO_KEYS:
        value = message.get(key)
        if isinstance(value, dict):
            return value
    return {}


def has_audio_message(message: dict[str, Any]) -> bool:
    return bool(extract_audio_descriptor(message))


async def load_audio_content(
    *,
    message: dict[str, Any],
    instance: str,
    message_key: dict[str, Any],
    evolution_url: str,
    evolution_api_key: str,
) -> TranscriptionResult | AudioContent:
    """Load audio bytes from direct base64, media URL or Evolution media endpoint."""
    descriptor = extract_audio_descriptor(message)
    if not descriptor:
        return TranscriptionResult(ok=False, code="no_audio")

    content_type = str(descriptor.get("mimetype") or "audio/ogg").split(";", 1)[0]
    if content_type.lower() not in _allowed_mime_types():
        return TranscriptionResult(ok=False, code="unsupported_mime_type")
    filename = _filename_for_content_type(content_type)
    message_id = str(message_key.get("id") or "")
    duration_seconds = _duration_seconds(descriptor)

    direct_base64 = _first_string(
        descriptor,
        ("base64", "mediaBase64", "file", "data"),
    )
    if direct_base64:
        return _decode_base64_audio(
            direct_base64,
            filename=filename,
            content_type=content_type,
            message_id=message_id,
            duration_seconds=duration_seconds,
        )

    media_url = _first_string(descriptor, ("url", "mediaUrl", "directPath"))
    if media_url and media_url.startswith(("http://", "https://")):
        return await _download_audio_url(
            media_url,
            filename=filename,
            content_type=content_type,
            message_id=message_id,
            duration_seconds=duration_seconds,
        )

    return await _load_audio_from_evolution(
        instance=instance,
        message_key=message_key,
        evolution_url=evolution_url,
        evolution_api_key=evolution_api_key,
        filename=filename,
        content_type=content_type,
        message_id=message_id,
        duration_seconds=duration_seconds,
    )


async def transcribe_audio(audio: AudioContent) -> TranscriptionResult:
    """Transcribe audio with configured provider and safe fallback behavior."""
    if len(audio.bytes_data) > _max_audio_bytes():
        return TranscriptionResult(
            ok=False,
            code="audio_too_large",
            fallback_message="Esse audio ficou muito longo para eu ouvir aqui. Pode me mandar por texto?",
        )

    primary = _provider_name()
    result = await _transcribe_with_provider(primary, audio)
    if result.ok:
        return result

    fallback = _fallback_provider_name()
    if fallback and fallback != "disabled" and fallback != primary:
        fallback_result = await _transcribe_with_provider(fallback, audio)
        if fallback_result.ok:
            return fallback_result
        return fallback_result

    return result


async def _transcribe_with_provider(provider: str, audio: AudioContent) -> TranscriptionResult:
    if provider == "disabled":
        return TranscriptionResult(ok=False, code="provider_disabled")
    if provider == "groq":
        return await _transcribe_with_groq(audio)
    return TranscriptionResult(ok=False, code="unsupported_provider")


async def _transcribe_with_groq(audio: AudioContent) -> TranscriptionResult:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return TranscriptionResult(ok=False, code="missing_groq_api_key")

    model = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo").strip()
    language = os.getenv("TRANSCRIPTION_LANGUAGE", "pt").strip()
    data = {
        "model": model,
        "response_format": "json",
        "temperature": "0",
    }
    if language:
        data["language"] = language

    files = {
        "file": (
            audio.filename,
            audio.bytes_data,
            audio.content_type or "application/octet-stream",
        )
    }
    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as client:
            response = await client.post(
                GROQ_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files=files,
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Audio transcription provider failed",
            extra={"provider": "groq", "status": exc.response.status_code},
        )
        return TranscriptionResult(ok=False, code=f"provider_http_{exc.response.status_code}")
    except Exception as exc:
        logger.warning(
            "Audio transcription provider unavailable",
            extra={"provider": "groq", "error_type": type(exc).__name__},
        )
        return TranscriptionResult(ok=False, code="provider_error")

    text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        return TranscriptionResult(ok=False, code="empty_transcription")
    return TranscriptionResult(ok=True, text=text)


def _decode_base64_audio(
    value: str,
    *,
    filename: str,
    content_type: str,
    message_id: str,
    duration_seconds: int | None,
) -> TranscriptionResult | AudioContent:
    try:
        payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        data = base64.b64decode(payload, validate=True)
    except Exception:
        return TranscriptionResult(ok=False, code="invalid_base64")
    return AudioContent(
        bytes_data=data,
        filename=filename,
        content_type=content_type,
        message_id=message_id,
        duration_seconds=duration_seconds,
    )


async def _download_audio_url(
    url: str,
    *,
    filename: str,
    content_type: str,
    message_id: str,
    duration_seconds: int | None,
) -> TranscriptionResult | AudioContent:
    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as client:
            response = await client.get(url)
            response.raise_for_status()
            return AudioContent(
                bytes_data=response.content,
                filename=filename,
                content_type=response.headers.get("content-type", content_type).split(";", 1)[0],
                message_id=message_id,
                duration_seconds=duration_seconds,
            )
    except Exception as exc:
        logger.warning("Audio media URL download failed", extra={"error_type": type(exc).__name__})
        return TranscriptionResult(ok=False, code="media_download_failed")


async def _load_audio_from_evolution(
    *,
    instance: str,
    message_key: dict[str, Any],
    evolution_url: str,
    evolution_api_key: str,
    filename: str,
    content_type: str,
    message_id: str,
    duration_seconds: int | None,
) -> TranscriptionResult | AudioContent:
    if not evolution_url or not evolution_api_key or not instance or not message_key:
        return TranscriptionResult(ok=False, code="missing_media_source")

    payload = {"message": {"key": message_key}, "convertToMp4": False}
    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as client:
            response = await client.post(
                f"{evolution_url.rstrip('/')}/chat/getBase64FromMediaMessage/{instance}",
                headers={"apikey": evolution_api_key, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.warning("Evolution audio media fetch failed", extra={"error_type": type(exc).__name__})
        return TranscriptionResult(ok=False, code="evolution_media_fetch_failed")

    base64_value = _find_base64_value(body)
    if not base64_value:
        return TranscriptionResult(ok=False, code="missing_media_base64")
    return _decode_base64_audio(
        base64_value,
        filename=filename,
        content_type=content_type,
        message_id=message_id,
        duration_seconds=duration_seconds,
    )


def _find_base64_value(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    for key in ("base64", "mediaBase64", "data"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    nested = payload.get("response") or payload.get("message")
    return _find_base64_value(nested)


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _filename_for_content_type(content_type: str) -> str:
    extension = {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/mp4": "m4a",
        "audio/ogg": "ogg",
        "audio/opus": "ogg",
        "audio/wav": "wav",
        "audio/webm": "webm",
    }.get(content_type, "ogg")
    return f"whatsapp-audio.{extension}"


def _duration_seconds(descriptor: dict[str, Any]) -> int | None:
    raw = descriptor.get("seconds") or descriptor.get("duration")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
