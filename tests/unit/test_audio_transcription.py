"""Unit tests for WhatsApp audio transcription helpers."""
from __future__ import annotations

import base64
import socket
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from zwaf.audio.transcription import (
    AudioContent,
    TranscriptionResult,
    _download_audio_url,
    _load_audio_from_evolution,
    load_audio_content,
    transcribe_audio,
)


def _patch_resolve_public(monkeypatch, ip: str = "93.184.216.34") -> None:
    """Make socket.getaddrinfo resolve any host to a fixed public IP."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _stream_client(chunks, *, status_ok=True, content_type="audio/ogg"):
    """Build a mock httpx.AsyncClient whose .stream() yields the given chunks."""
    response = MagicMock()
    response.headers = {"content-type": content_type}
    if status_ok:
        response.raise_for_status = MagicMock()
    else:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock())
        )

    async def aiter_bytes():
        for chunk in chunks:
            yield chunk

    response.aiter_bytes = aiter_bytes

    stream_ctx = AsyncMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=response)
    stream_ctx.__aexit__ = AsyncMock(return_value=False)

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.stream = MagicMock(return_value=stream_ctx)
    return client


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


@pytest.mark.asyncio
async def test_download_audio_url_success_streams_bytes(monkeypatch):
    _patch_resolve_public(monkeypatch)
    client = _stream_client([b"part-1", b"part-2"], content_type="audio/ogg; codecs=opus")
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        "https://media.example.com/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=3,
    )

    assert isinstance(result, AudioContent)
    assert result.bytes_data == b"part-1part-2"
    assert result.content_type == "audio/ogg"
    assert result.message_id == "msg-1"


@pytest.mark.asyncio
async def test_download_audio_url_rejects_host_not_in_allowlist(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_URL_ALLOWED_HOSTS", "media.example.com")
    _patch_resolve_public(monkeypatch)
    # Should never reach the network — guard the client to prove it.
    client = _stream_client([b"x"])
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        "https://evil.example.org/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "blocked_url_host"
    client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_download_audio_url_allows_host_in_allowlist(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_URL_ALLOWED_HOSTS", "media.example.com")
    _patch_resolve_public(monkeypatch)
    client = _stream_client([b"ok-bytes"])
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        "https://media.example.com/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, AudioContent)
    assert result.bytes_data == b"ok-bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, resolved_ip",
    [
        ("http://127.0.0.1/x", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data", "169.254.169.254"),
        ("http://intranet.local/audio.ogg", "10.0.0.5"),
    ],
)
async def test_download_audio_url_rejects_private_or_loopback_ip(monkeypatch, url, resolved_ip):
    _patch_resolve_public(monkeypatch, ip=resolved_ip)
    client = _stream_client([b"x"])
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        url,
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "blocked_url_ip"
    client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_download_audio_url_rejects_non_http_scheme(monkeypatch):
    _patch_resolve_public(monkeypatch)

    result = await _download_audio_url(
        "ftp://media.example.com/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "blocked_url_scheme"


@pytest.mark.asyncio
async def test_download_audio_url_aborts_when_exceeding_max_bytes(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_MAX_BYTES", "5")
    _patch_resolve_public(monkeypatch)
    client = _stream_client([b"123", b"456", b"789"])
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        "https://media.example.com/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "media_too_large"


@pytest.mark.asyncio
async def test_download_audio_url_timeout_returns_fallback(monkeypatch):
    _patch_resolve_public(monkeypatch)

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    def raising_stream(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    client.stream = MagicMock(side_effect=raising_stream)
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _download_audio_url(
        "https://media.example.com/audio.ogg",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "media_download_failed"


@pytest.mark.asyncio
async def test_load_audio_from_evolution_success(monkeypatch):
    audio_bytes = b"evolution-audio"
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"base64": base64.b64encode(audio_bytes).decode("ascii")}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _load_audio_from_evolution(
        instance="livia-test",
        message_key={"id": "msg-1"},
        evolution_url="http://evolution-api:8080",
        evolution_api_key="test-key",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=2,
    )

    assert isinstance(result, AudioContent)
    assert result.bytes_data == audio_bytes
    url_arg, _ = client.post.call_args
    assert "getBase64FromMediaMessage/livia-test" in url_arg[0]


@pytest.mark.asyncio
async def test_load_audio_from_evolution_failure_returns_fallback(monkeypatch):
    response = MagicMock()
    response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock())
    )
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: client)

    result = await _load_audio_from_evolution(
        instance="livia-test",
        message_key={"id": "msg-1"},
        evolution_url="http://evolution-api:8080",
        evolution_api_key="test-key",
        filename="whatsapp-audio.ogg",
        content_type="audio/ogg",
        message_id="msg-1",
        duration_seconds=None,
    )

    assert isinstance(result, TranscriptionResult)
    assert result.ok is False
    assert result.code == "evolution_media_fetch_failed"
