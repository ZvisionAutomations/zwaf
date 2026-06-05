"""TDD — testes para WhatsAppTool com throttle fix."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zwaf.tools.base import RateLimitError, ToolResult
from zwaf.tools.whatsapp import (
    MessageQueue,
    PhoneRateLimiter,
    WhatsAppTool,
    get_warm_up_limit,
)


# ─────────────────────────────────────────────────────────────
# get_warm_up_limit
# ─────────────────────────────────────────────────────────────

class TestGetWarmUpLimit:
    def test_day_1_limit_is_20(self):
        assert get_warm_up_limit(1, messages_per_minute=10) == 20

    def test_day_2_limit_is_20(self):
        assert get_warm_up_limit(2, messages_per_minute=10) == 20

    def test_day_3_limit_is_20(self):
        assert get_warm_up_limit(3, messages_per_minute=10) == 20

    def test_day_4_limit_is_50(self):
        assert get_warm_up_limit(4, messages_per_minute=10) == 50

    def test_day_7_limit_is_50(self):
        assert get_warm_up_limit(7, messages_per_minute=10) == 50

    def test_day_8_normal_operation(self):
        assert get_warm_up_limit(8, messages_per_minute=10) == 10 * 60 * 8

    def test_day_10_normal_operation(self):
        assert get_warm_up_limit(10, messages_per_minute=5) == 5 * 60 * 8

    def test_day_8_plus_uses_messages_per_minute(self):
        assert get_warm_up_limit(20, messages_per_minute=20) == 20 * 60 * 8


# ─────────────────────────────────────────────────────────────
# PhoneRateLimiter
# ─────────────────────────────────────────────────────────────

class TestPhoneRateLimiter:
    def test_initial_count_zero(self):
        limiter = PhoneRateLimiter(messages_per_minute=10)
        assert limiter.count_last_minute("5511999990001") == 0

    def test_acquire_increments_count(self):
        limiter = PhoneRateLimiter(messages_per_minute=10)
        limiter.record_sent("5511999990001")
        assert limiter.count_last_minute("5511999990001") == 1

    def test_is_under_limit_when_below_max(self):
        limiter = PhoneRateLimiter(messages_per_minute=10)
        for _ in range(5):
            limiter.record_sent("5511999990001")
        assert limiter.is_under_limit("5511999990001") is True

    def test_is_at_limit_when_at_max(self):
        limiter = PhoneRateLimiter(messages_per_minute=3)
        for _ in range(3):
            limiter.record_sent("5511999990001")
        assert limiter.is_under_limit("5511999990001") is False

    def test_different_numbers_tracked_independently(self):
        limiter = PhoneRateLimiter(messages_per_minute=2)
        for _ in range(2):
            limiter.record_sent("5511999990001")
        assert limiter.is_under_limit("5511999990001") is False
        assert limiter.is_under_limit("5511999990002") is True


# ─────────────────────────────────────────────────────────────
# MessageQueue
# ─────────────────────────────────────────────────────────────

class TestMessageQueue:
    @pytest.mark.asyncio
    async def test_queue_processes_messages_in_order(self):
        queue = MessageQueue()
        processed = []

        async def fake_send(phone, text):
            processed.append((phone, text))
            return ToolResult.ok({"status": "sent"})

        await queue.enqueue("5511999990001", "msg1", fake_send)
        await queue.enqueue("5511999990001", "msg2", fake_send)
        assert processed == [("5511999990001", "msg1"), ("5511999990001", "msg2")]

    @pytest.mark.asyncio
    async def test_queue_does_not_lose_messages_on_error(self):
        queue = MessageQueue()
        call_count = [0]

        async def flaky_send(phone, text):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("transient error")
            return ToolResult.ok({"status": "sent"})

        # Queue retries — message not lost
        result = await queue.enqueue("5511999990001", "important", flaky_send)
        assert result.success is True
        assert call_count[0] == 2


# ─────────────────────────────────────────────────────────────
# WhatsAppTool — 429 handler
# ─────────────────────────────────────────────────────────────

class TestWhatsAppTypingSimulation:
    def test_default_typing_delay_preserves_current_bounds(self):
        tool = WhatsAppTool()
        assert tool._typing_delay_ms(0) == 1000
        assert tool._typing_delay_ms(49) == 1000
        assert tool._typing_delay_ms(100) == 2000
        assert tool._typing_delay_ms(1000) == 5000

    def test_custom_typing_delay_uses_tenant_config(self):
        tool = WhatsAppTool(
            typing_min_ms=1500,
            typing_max_ms=7000,
            typing_chars_per_second=25,
            typing_jitter_ms=0,
        )
        assert tool._typing_delay_ms(10) == 1500
        assert tool._typing_delay_ms(100) == 4000
        assert tool._typing_delay_ms(1000) == 7000

    def test_typing_jitter_is_clamped(self):
        tool = WhatsAppTool(
            typing_min_ms=1000,
            typing_max_ms=2000,
            typing_chars_per_second=50,
            typing_jitter_ms=1000,
        )
        with patch("zwaf.tools.whatsapp.random.randint", return_value=1000):
            assert tool._typing_delay_ms(100) == 2000

    @pytest.mark.asyncio
    async def test_set_typing_posts_configured_delay(self):
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
            typing_min_ms=1500,
            typing_max_ms=7000,
            typing_chars_per_second=25,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await tool._set_typing("5511999990001", 100)

        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["delay"] == 4000

    @pytest.mark.asyncio
    async def test_send_raw_includes_send_text_delay(self):
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
            send_text_delay_ms=800,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"key": {"id": "msg-1"}}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await tool._send_raw("5511999990001", "test message")

        assert result.success is True
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["delay"] == 800


class TestWhatsAppTool429Handler:
    @pytest.mark.asyncio
    async def test_429_triggers_rate_limit_error(self):
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
        )

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"

        import httpx
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response_obj = MagicMock()
            mock_response_obj.status_code = 429
            mock_response_obj.text = "Too Many Requests"
            http_error = httpx.HTTPStatusError(
                "429", request=MagicMock(), response=mock_response_obj
            )
            mock_client.post = AsyncMock(side_effect=http_error)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RateLimitError):
                await tool._send_raw("5511999990001", "test message")

    @pytest.mark.asyncio
    async def test_429_backoff_is_at_least_30s(self):
        """Valida que o backoff para 429 é >= 30s (testado via mock do asyncio.sleep)."""
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
        )

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        call_count = [0]

        async def mock_send_raw(phone, text):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RateLimitError("429")
            return ToolResult.ok({"status": "sent"})

        with patch("zwaf.tools.whatsapp.asyncio.sleep", mock_sleep):
            with patch.object(tool, "_send_raw", mock_send_raw):
                result = await tool.send_message(
                    phone="5511999990001",
                    text="test",
                    session_id="sess-001",
                )

        assert result.success is True
        assert len(sleep_calls) >= 1
        assert sleep_calls[0] >= 30.0, f"Backoff too short: {sleep_calls[0]}s (expected >= 30s)"

    @pytest.mark.asyncio
    async def test_5xx_uses_normal_retry_not_429_backoff(self):
        """5xx deve usar backoff normal (1s→2s→4s), não o backoff 429."""
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
        )

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        import httpx

        call_count = [0]

        async def mock_send_raw(phone, text):
            call_count[0] += 1
            if call_count[0] < 3:
                mock_resp = MagicMock()
                mock_resp.status_code = 503
                mock_resp.text = "Service Unavailable"
                raise httpx.HTTPStatusError("503", request=MagicMock(), response=mock_resp)
            return ToolResult.ok({"status": "sent"})

        with patch("zwaf.tools.whatsapp.asyncio.sleep", mock_sleep):
            with patch.object(tool, "_send_raw", mock_send_raw):
                result = await tool.send_message(
                    phone="5511999990001",
                    text="test",
                    session_id="sess-001",
                )

        assert result.success is True
        # Normal backoff: 1s, 2s (not >= 30s)
        assert all(s < 30.0 for s in sleep_calls), f"5xx backoff should be < 30s, got {sleep_calls}"


# ─────────────────────────────────────────────────────────────
# WhatsAppTool — warm-up limit
# ─────────────────────────────────────────────────────────────

class TestWhatsAppToolWarmUp:
    @pytest.mark.asyncio
    async def test_warm_up_blocks_over_daily_limit(self):
        """Warm-up day 1: limite 20 msgs/dia. Msg 21 deve falhar."""
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
            warm_up_mode=True,
            warm_up_day=1,
            messages_per_minute=10,
        )
        # Simular que 20 mensagens já foram enviadas hoje
        tool._daily_sent_count = 20

        result = await tool.send_message(
            phone="5511999990001",
            text="excess message",
            session_id="sess-001",
        )
        assert result.success is False
        assert "limite" in result.error.lower() or "limit" in result.error.lower()

    @pytest.mark.asyncio
    async def test_warm_up_allows_under_daily_limit(self):
        tool = WhatsAppTool(
            base_url="http://localhost:8080",
            api_key="test-key",
            instance="test-1",
            warm_up_mode=True,
            warm_up_day=1,
            messages_per_minute=10,
        )
        tool._daily_sent_count = 0

        async def mock_send_raw(phone, text):
            return ToolResult.ok({"status": "sent", "message_id": "abc"})

        with patch.object(tool, "_send_raw", mock_send_raw):
            result = await tool.send_message(
                phone="5511999990001",
                text="valid message",
                session_id="sess-001",
            )
        assert result.success is True


# ─────────────────────────────────────────────────────────────
# WhatsAppTool — rotate_number
# ─────────────────────────────────────────────────────────────

class TestWhatsAppToolRotation:
    def test_rotate_number_returns_next_available(self):
        from zwaf.core.tenant import PhoneNumberEntry
        numbers = [
            PhoneNumberEntry(number="5511999990001", instance="inst-1"),
            PhoneNumberEntry(number="5511999990002", instance="inst-2"),
        ]
        tool = WhatsAppTool.from_phone_entries(numbers)
        first = tool.current_instance
        tool.rotate_number()
        assert tool.current_instance != first

    def test_rotate_warns_if_all_cooling(self):
        from zwaf.core.tenant import PhoneNumberEntry
        numbers = [PhoneNumberEntry(number="5511999990001", instance="inst-1")]
        tool = WhatsAppTool.from_phone_entries(numbers)
        tool.mark_cooling("inst-1")

        with patch("zwaf.tools.whatsapp.logger") as mock_logger:
            tool.rotate_number()
            mock_logger.warning.assert_called()
