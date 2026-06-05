"""Base tool com retry, tracing e error handling — ZWAF fork da Sofia SDR."""
from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Constante: backoff mínimo para HTTP 429 (em segundos)
HTTP_429_MIN_BACKOFF = 30.0
HTTP_429_MAX_JITTER = 10.0


def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """
    Decorator para retry com exponential backoff (erros de rede e 5xx).
    NÃO deve ser usado para HTTP 429 — use with_429_retry.
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Tool call failed, retrying",
                            extra={
                                "tool": func.__name__,
                                "attempt": attempt + 1,
                                "delay": delay,
                                "error": str(e),
                            },
                        )
                        await asyncio.sleep(delay)
            logger.error(
                "Tool call failed after all retries",
                extra={"tool": func.__name__, "error": str(last_error)},
            )
            raise last_error  # type: ignore
        return wrapper  # type: ignore
    return decorator


def with_429_retry(max_attempts: int = 3):
    """
    Decorator para retry específico de HTTP 429 (rate limit da Evolution API).

    Comportamento separado do with_retry:
    - Detecta RateLimitError (marcado pelo WhatsAppTool)
    - Backoff mínimo: HTTP_429_MIN_BACKOFF (30s) + jitter aleatório (0-10s)
    - Não usa exponential backoff — o rate limit tem duração fixa
    - Após max_attempts, lança o erro sem engolir
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except RateLimitError as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        jitter = random.uniform(0, HTTP_429_MAX_JITTER)
                        backoff = HTTP_429_MIN_BACKOFF + jitter
                        logger.warning(
                            "HTTP 429 rate limit hit — backing off",
                            extra={
                                "tool": func.__name__,
                                "attempt": attempt + 1,
                                "backoff_seconds": round(backoff, 1),
                            },
                        )
                        await asyncio.sleep(backoff)
            logger.error(
                "Rate limit persisted after all retries",
                extra={"tool": func.__name__, "attempts": max_attempts},
            )
            raise last_error  # type: ignore
        return wrapper  # type: ignore
    return decorator


class RateLimitError(Exception):
    """Levantada quando a Evolution API retorna HTTP 429."""


class ToolResult:
    """Resultado padronizado de tool call."""

    def __init__(self, success: bool, data: Any = None, error: str | None = None):
        self.success = success
        self.data = data
        self.error = error

    @classmethod
    def ok(cls, data: Any = None) -> "ToolResult":
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(success=False, error=error)

    def to_dict(self) -> dict:
        if self.success:
            return {"success": True, "data": self.data}
        return {"success": False, "error": self.error}


class BaseTool:
    """Classe base para todas as ferramentas do ZWAF."""

    name: str = "base_tool"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.logger = logging.getLogger(f"zwaf.tools.{self.name}")

    async def _execute_with_timeout(self, coro: Any) -> Any:
        return await asyncio.wait_for(coro, timeout=self.timeout)
