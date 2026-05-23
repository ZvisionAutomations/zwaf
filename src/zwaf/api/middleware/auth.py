"""Auth middleware — API Key + tenant_id validation."""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_API_KEYS = set(filter(None, os.getenv("ZWAF_API_KEYS", "").split(",")))

# Rotas públicas que não precisam de autenticação
_PUBLIC_PATHS = {"/health", "/metrics", "/docs", "/redoc", "/openapi.json"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Rotas públicas: sem auth
        if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Sem chaves configuradas: aceita tudo (dev mode)
        if not _API_KEYS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ")

        if not api_key or api_key not in _API_KEYS:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized — invalid or missing API key"},
            )

        return await call_next(request)
