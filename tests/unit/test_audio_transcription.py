"""Unit tests for WhatsApp audio transcription helpers."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from zwaf.audio.transcription import (
    AudioContent,
    TranscriptionResult,
    load_audio_content,
    transcribe_audio,
)


@pytest.mark.asyncio
async def test_load_audio_content_decodes_direct_base64_audio():
    audio_bytes = b"fake-audio"
    result = await load_audio_content(
        message={
            "audioMessage": {
                "mimetype": "audio/ogg; codecs=opus",
                "base64": base64.b64encode(audio_bytes).decode("ascii"),
                "seconds": 3,
            }
        },
        instance="livia-test",
        message_key={"id": "msg-1"},
        evolution_url="http://evolution-api:8080",
        evolution_api_key="test-key",
    )

    assert isinstance(result, AudioContent)
    assert result.bytes_data == audio_bytes
    assert result.content_type == "audio/ogg"
    assert result.message_id == "msg-1"
    assert result.duration_seconds == 3


@pytest.mark.asyncio
async def test_load_audio_content_rejects_unsupported_mime(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_ALLOWED_MIME_TYPES", "audio/ogg")

    result = await load_audio_content(
        message={"audioMessage": {"mimetype": "video/mp4", "base64": "AAAA"}},
        instance="livia-test",
        message_key={"id": "msg-1"},
        evolution_url="http://evolution-api:8080",
        evolution_api_key="test-key",
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "unsupported_mime_type"


@pytest.mark.asyncio
async def test_transcribe_audio_disabled_provider_does_not_call_network(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "disabled")

    result = await transcribe_audio(AudioContent(bytes_data=b"fake-audio"))

    assert result.ok is False
    assert result.code == "provider_disabled"


@pytest.mark.asyncio
async def test_transcribe_audio_rejects_large_audio_before_provider(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("TRANSCRIPTION_MAX_BYTES", "3")

    result = await transcribe_audio(AudioContent(bytes_data=b"too-large"))

    assert result.ok is False
    assert result.code == "audio_too_large"


@pytest.mark.asyncio
async def test_groq_transcription_posts_audio_without_logging_payload(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"text": "quero comprar um pote"}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)

    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await transcribe_audio(
        AudioContent(bytes_data=b"fake-audio", filename="audio.ogg", content_type="audio/ogg")
    )

    assert result.ok is True
    assert result.text == "quero comprar um pote"
    _, kwargs = client.post.call_args
    assert kwargs["data"]["model"] == "whisper-large-v3-turbo"
    assert kwargs["files"]["file"][1] == b"fake-audio"
