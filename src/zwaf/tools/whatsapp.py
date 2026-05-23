"""
WhatsApp Tool — Evolution API com throttle fix.

Correções vs Sofia SDR:
- asyncio.Queue por instância (1 message in-flight por vez)
- Handler HTTP 429 separado: backoff mínimo 30s + jitter, sem retry imediato
- Rate limiter configurável: messages_per_minute por número
- Warm-up mode: limita volume por dia conforme fase (20/50/normal)
- Rotação de número quando limite diário atingido
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from typing import Optional, Sequence

import httpx

from .base import BaseTool, HTTP_429_MIN_BACKOFF, HTTP_429_MAX_JITTER, RateLimitError, ToolResult

logger = logging.getLogger("zwaf.tools.whatsapp")


def _normalize_phone(phone: str) -> str:
    """Remove +, espaços e hífens. Evolution API espera só dígitos."""
    return re.sub(r"[^\d]", "", phone)


def get_warm_up_limit(day: int, messages_per_minute: int) -> int:
    """
    Retorna o limite diário de mensagens conforme fase de warm-up.

    - dia 1-3:  20 mensagens/dia
    - dia 4-7:  50 mensagens/dia
    - dia 8+:   messages_per_minute * 60 * 8 (jornada 8h)
    """
    if day <= 3:
        return 20
    if day <= 7:
        return 50
    return messages_per_minute * 60 * 8


class PhoneRateLimiter:
    """
    Rate limiter por número: rastreia timestamps de envio nos últimos 60s.
    Thread-safe para asyncio (single-threaded event loop).
    """

    def __init__(self, messages_per_minute: int):
        self._limit = messages_per_minute
        # deque de timestamps por número
        self._windows: dict[str, deque] = defaultdict(deque)

    def _prune(self, number: str) -> None:
        """Remove timestamps com mais de 60 segundos."""
        cutoff = time.monotonic() - 60.0
        window = self._windows[number]
        while window and window[0] < cutoff:
            window.popleft()

    def count_last_minute(self, number: str) -> int:
        self._prune(number)
        return len(self._windows[number])

    def is_under_limit(self, number: str) -> bool:
        return self.count_last_minute(number) < self._limit

    def record_sent(self, number: str) -> None:
        self._prune(number)
        self._windows[number].append(time.monotonic())


class MessageQueue:
    """
    asyncio.Queue por número: garante 1 mensagem in-flight por vez.
    Previne envios simultâneos para o mesmo número (problema de order + throttle).
    """

    def __init__(self):
        # Lock por número — garante serialização de envios
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def enqueue(self, phone: str, text: str, send_fn) -> ToolResult:
        """
        Serializa envio para o número via lock.
        Garante que send_fn(phone, text) seja chamado um por vez por número.
        Retry automático em caso de erro transiente (max 2 tentativas).
        """
        async with self._locks[phone]:
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    return await send_fn(phone, text)
                except RateLimitError:
                    raise  # 429 sobe para o with_429_retry handler
                except Exception as e:
                    last_err = e
                    if attempt == 0:
                        await asyncio.sleep(1.0)
            logger.error(
                "MessageQueue: all retries failed",
                extra={"phone_tail": phone[-4:], "error": str(last_err)},
            )
            raise last_err  # type: ignore


class WhatsAppTool(BaseTool):
    """
    Ferramenta WhatsApp para o ZWAF.

    Suporta:
    - Uma instância Evolution API por número (configuração ZWAF)
    - Rate limiting por número
    - asyncio.Queue (serialização de envios)
    - HTTP 429: backoff 30s+ (NÃO usa o retry padrão)
    - HTTP 5xx: backoff normal (1s→2s→4s)
    - Warm-up mode: limite diário crescente por fase
    - Typing simulation antes do envio
    - Rotação de número quando limite atingido
    """

    name = "whatsapp"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        instance: str = "",
        messages_per_minute: int = 10,
        typing_simulation: bool = True,
        warm_up_mode: bool = False,
        warm_up_day: Optional[int] = None,
        timeout: float = 10.0,
        # Suporte a múltiplos números/instâncias
        _phone_entries: Optional[list] = None,
    ):
        super().__init__(timeout=timeout)
        self.base_url = (base_url or os.getenv("EVOLUTION_API_URL", "http://localhost:8080")).rstrip("/")
        self.api_key = api_key or os.getenv("EVOLUTION_API_KEY", "")
        self.instance = instance or os.getenv("EVOLUTION_INSTANCE", "zwaf")
        self.messages_per_minute = messages_per_minute
        self.typing_simulation = typing_simulation
        self.warm_up_mode = warm_up_mode
        self.warm_up_day = warm_up_day

        # Rate limiter + queue compartilhados
        self._rate_limiter = PhoneRateLimiter(messages_per_minute)
        self._queue = MessageQueue()

        # Controle de warm-up: contador diário
        self._daily_sent_count = 0
        self._daily_count_date: Optional[str] = None  # ISO date do último reset

        # Multi-número: lista de (number, instance) e índice corrente
        self._phone_entries: list = list(_phone_entries or [])
        self._current_entry_idx: int = 0
        self._cooling: set[str] = set()  # instâncias em cooling

    # ─── Construtor alternativo para múltiplos números ─────

    @classmethod
    def from_phone_entries(cls, entries: list, **kwargs) -> "WhatsAppTool":
        if not entries:
            raise ValueError("phone_entries must not be empty")
        first = entries[0]
        return cls(
            instance=first.instance,
            _phone_entries=entries,
            **kwargs,
        )

    @property
    def current_instance(self) -> str:
        if self._phone_entries and self._current_entry_idx < len(self._phone_entries):
            return self._phone_entries[self._current_entry_idx].instance
        return self.instance

    def rotate_number(self) -> None:
        """
        Avança para o próximo número disponível da lista.
        Marca número atual como cooling se atingiu limite diário.
        Loga warning se todos os números estão em cooling.
        """
        if not self._phone_entries:
            return

        available = [
            i for i, e in enumerate(self._phone_entries)
            if e.instance not in self._cooling
        ]

        if not available:
            logger.warning(
                "All phone numbers are in cooling — rate limit reached for all instances",
                extra={"tenant_cooling_count": len(self._cooling)},
            )
            return

        # Round-robin entre disponíveis
        next_idx = (self._current_entry_idx + 1) % len(self._phone_entries)
        while next_idx not in available:
            next_idx = (next_idx + 1) % len(self._phone_entries)
        self._current_entry_idx = next_idx

    def mark_cooling(self, instance: str) -> None:
        """Marca instância como cooling (limite diário atingido)."""
        self._cooling.add(instance)

    # ─── Limite de warm-up ────────────────────────────────

    def _daily_limit(self) -> Optional[int]:
        if not self.warm_up_mode or self.warm_up_day is None:
            return None
        return get_warm_up_limit(self.warm_up_day, self.messages_per_minute)

    def _reset_daily_count_if_needed(self) -> None:
        from datetime import date
        today = date.today().isoformat()
        if self._daily_count_date != today:
            self._daily_sent_count = 0
            self._daily_count_date = today

    def _check_warm_up_limit(self) -> Optional[ToolResult]:
        self._reset_daily_count_if_needed()
        limit = self._daily_limit()
        if limit is not None and self._daily_sent_count >= limit:
            return ToolResult.fail(
                f"Warm-up daily limit reached: {self._daily_sent_count}/{limit} messages sent today (day {self.warm_up_day})"
            )
        return None

    # ─── Envio principal ──────────────────────────────────

    async def send_message(
        self,
        phone: str,
        text: str,
        session_id: Optional[str] = None,
    ) -> ToolResult:
        """
        Envia mensagem via Evolution API com:
        1. Checagem de warm-up limit
        2. Rate limit check
        3. Queue serializada por número
        4. Typing simulation
        5. Retry 429 (backoff >= 30s) separado de 5xx (backoff normal)
        """
        if not self.api_key:
            logger.warning("whatsapp_noop_unconfigured")
            return ToolResult.ok({"status": "noop_unconfigured", "message_id": None})

        limit_result = self._check_warm_up_limit()
        if limit_result is not None:
            return limit_result

        number = _normalize_phone(phone)

        async def _do_send(phone: str, text: str) -> ToolResult:
            return await self._send_with_429_retry(phone, text, session_id)

        result = await self._queue.enqueue(number, text, _do_send)
        if result.success:
            self._daily_sent_count += 1
            self._rate_limiter.record_sent(number)
        return result

    async def _send_with_429_retry(
        self,
        phone: str,
        text: str,
        session_id: Optional[str],
        max_attempts: int = 3,
    ) -> ToolResult:
        """
        Camada de retry específica para 429.
        Separa o handling de 429 (backoff 30s+) de 5xx (backoff normal).
        """
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                if self.typing_simulation:
                    await self._set_typing(phone, len(text))
                return await self._send_raw_with_5xx_retry(phone, text)
            except RateLimitError as e:
                last_err = e
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0, HTTP_429_MAX_JITTER)
                    backoff = HTTP_429_MIN_BACKOFF + jitter
                    logger.warning(
                        "HTTP 429 rate limit — backing off",
                        extra={
                            "phone_tail": phone[-4:],
                            "backoff_seconds": round(backoff, 1),
                            "attempt": attempt + 1,
                        },
                    )
                    await asyncio.sleep(backoff)
        logger.error(
            "Rate limit persisted after all retry attempts",
            extra={"phone_tail": phone[-4:], "attempts": max_attempts},
        )
        return ToolResult.fail(f"Rate limit (HTTP 429) persisted after {max_attempts} attempts")

    async def _send_raw_with_5xx_retry(
        self,
        phone: str,
        text: str,
        max_attempts: int = 3,
        base_delay: float = 1.0,
    ) -> ToolResult:
        """Retry para 5xx com exponential backoff (1s→2s→4s). NÃO captura 429."""
        import httpx
        last_err: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return await self._send_raw(phone, text)
            except RateLimitError:
                raise  # 429 sobe para _send_with_429_retry
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    raise RateLimitError(f"HTTP 429: {e.response.text[:100]}") from e
                last_err = e
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Evolution API HTTP error — retrying",
                        extra={"status": e.response.status_code, "delay": delay, "attempt": attempt + 1},
                    )
                    await asyncio.sleep(delay)
            except httpx.RequestError as e:
                last_err = e
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Evolution API connection error — retrying",
                        extra={"error": str(e), "delay": delay, "attempt": attempt + 1},
                    )
                    await asyncio.sleep(delay)
        logger.error(
            "Evolution API failed after all retries",
            extra={"phone_tail": phone[-4:], "error": str(last_err)},
        )
        return ToolResult.fail(f"Evolution API error after {max_attempts} attempts: {last_err}")

    async def _send_raw(self, phone: str, text: str) -> ToolResult:
        """POST direto para Evolution API — sem retry. Lança exceção em erro."""
        import httpx
        url = f"{self.base_url}/message/sendText/{self.current_instance}"
        payload = {"number": phone, "text": text}
        logger.info(
            "Sending WhatsApp via Evolution API",
            extra={"phone_tail": phone[-4:], "length": len(text), "instance": self.current_instance},
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code == 429:
                raise RateLimitError(f"HTTP 429: {resp.text[:100]}")
            resp.raise_for_status()
            data = resp.json()
            return ToolResult.ok({"message_id": data.get("key", {}).get("id", ""), "status": "sent"})

    async def _set_typing(self, phone: str, text_length: int) -> None:
        """Envia presença 'composing'. Best-effort — nunca bloqueia o envio."""
        import httpx
        duration = max(1, min(5, text_length // 50))
        url = f"{self.base_url}/chat/sendPresence/{self.current_instance}"
        payload = {"number": phone, "presence": "composing", "delay": duration * 1000}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
        except Exception:
            pass  # typing indicator é best-effort

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.api_key, "Content-Type": "application/json"}


# ─────────────────────────────────────────────────────────────
# Funções de conveniência para uso como Agno tools
# ─────────────────────────────────────────────────────────────

# Tool singleton global por tenant — inicializado pelo TenantLoader no lifespan
_tenant_tools: dict[str, WhatsAppTool] = {}


def get_whatsapp_tool(tenant_id: str) -> WhatsAppTool:
    if tenant_id not in _tenant_tools:
        raise RuntimeError(f"WhatsAppTool not initialized for tenant '{tenant_id}'")
    return _tenant_tools[tenant_id]


def register_whatsapp_tool(tenant_id: str, tool: WhatsAppTool) -> None:
    _tenant_tools[tenant_id] = tool
