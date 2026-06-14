from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zwaf.tools.base import RateLimitError, ToolResult
from zwaf.tools.whatsapp import WhatsAppTool


@pytest.mark.asyncio
async def test_send_image_posts_evolution_send_media_payload_from_url():
    tool = WhatsAppTool(
        base_url="http://localhost:8080",
        api_key="test-key",
        instance="livia-1",
        typing_simulation=False,
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"key": {"id": "media-msg-1"}}
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await tool.send_image(
            phone="+55 11 99999-0001",
            media_url="https://cdn.example.test/prova-1.jpg",
            caption="Foto real aprovada do New Woman.",
            session_id="sess-047",
            asset_id="livia_social_proof_001",
        )

    assert result.success is True
    assert result.data["message_id"] == "media-msg-1"
    args, kwargs = mock_client.post.call_args
    assert args[0] == "http://localhost:8080/message/sendMedia/livia-1"
    assert kwargs["json"] == {
        "number": "5511999990001",
        "mediatype": "image",
        "mimetype": "image/jpeg",
        "media": "https://cdn.example.test/prova-1.jpg",
        "caption": "Foto real aprovada do New Woman.",
        "fileName": "social-proof.jpg",
    }


@pytest.mark.asyncio
async def test_send_image_encodes_media_path():
    image_path = __file__
    tool = WhatsAppTool(
        base_url="http://localhost:8080",
        api_key="test-key",
        instance="livia-1",
        typing_simulation=False,
    )

    with patch.object(
        tool,
        "_send_media_raw_with_5xx_retry",
        AsyncMock(return_value=ToolResult.ok({"status": "sent"})),
    ) as send_mock:
        result = await tool.send_image(
            phone="5511999990001",
            media_path=str(image_path),
            caption="Foto real aprovada do New Woman.",
        )

    assert result.success is True
    payload = send_mock.call_args.args[0]
    assert payload["media"]
    assert payload["fileName"] == "test_whatsapp_media.py"
    assert payload["mimetype"] == "text/x-python"


@pytest.mark.asyncio
async def test_send_image_429_uses_media_backoff():
    tool = WhatsAppTool(api_key="test-key", typing_simulation=False)
    sleep_calls = []
    calls = {"count": 0}

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    async def flaky_media_send(payload, asset_id):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RateLimitError("429")
        return ToolResult.ok({"status": "sent"})

    with patch("zwaf.tools.whatsapp.asyncio.sleep", fake_sleep):
        with patch.object(tool, "_send_media_raw_with_5xx_retry", flaky_media_send):
            result = await tool.send_image(
                phone="5511999990001",
                media_url="https://cdn.example.test/prova-1.jpg",
                caption="Foto real aprovada do New Woman.",
            )

    assert result.success is True
    assert sleep_calls
    assert sleep_calls[0] >= 30.0
