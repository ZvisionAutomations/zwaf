"""Redis session store — rastreia estado de conversa e histórico de compra."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("zwaf.memory.session")

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def get_purchase_history(phone: str, tenant_id: str) -> bool:
    """Retorna True se o número tem histórico de compra registrado."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:lead:{phone}:purchased"
        result = await client.exists(key)
        await client.aclose()
        return bool(result)
    except Exception as e:
        logger.warning("Redis unavailable — assuming no purchase history: %s", e)
        return False


async def record_purchase(phone: str, tenant_id: str, product_id: str) -> None:
    """Registra uma compra no Redis."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:lead:{phone}:purchased"
        await client.set(key, product_id, ex=60 * 60 * 24 * 730)  # 2 anos (LGPD)
        await client.aclose()
    except Exception as e:
        logger.warning("Failed to record purchase in Redis: %s", e)


async def get_session_state(session_id: str, tenant_id: str) -> dict:
    """Retorna estado da sessão atual."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:session:{session_id}"
        data = await client.get(key)
        await client.aclose()
        return json.loads(data) if data else {}
    except Exception:
        return {}


async def set_session_state(session_id: str, tenant_id: str, state: dict, ttl_seconds: int = 3600) -> None:
    """Persiste estado da sessão."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:session:{session_id}"
        await client.setex(key, ttl_seconds, json.dumps(state))
        await client.aclose()
    except Exception as e:
        logger.warning("Failed to save session state: %s", e)


async def acquire_session_lock(
    *,
    tenant_id: str,
    session_id: str,
    lock_name: str,
    ttl_seconds: int = 15,
) -> bool:
    """Acquire a short Redis lock for session-scoped critical sections."""
    client: Optional[object] = None
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:session:{session_id}:lock:{lock_name}"
        acquired = await client.set(key, "1", ex=ttl_seconds, nx=True)
        return bool(acquired)
    except Exception as e:
        logger.warning("Redis lock unavailable; proceeding without lock: %s", e)
        return True
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


async def release_session_lock(
    *,
    tenant_id: str,
    session_id: str,
    lock_name: str,
) -> None:
    """Release a session-scoped Redis lock best-effort."""
    client: Optional[object] = None
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(_REDIS_URL)
        key = f"zwaf:{tenant_id}:session:{session_id}:lock:{lock_name}"
        await client.delete(key)
    except Exception as e:
        logger.warning("Failed to release Redis session lock: %s", e)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
