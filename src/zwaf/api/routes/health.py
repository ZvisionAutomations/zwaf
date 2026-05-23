"""Health check e métricas."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "service": "zwaf", "version": "1.0.0"}


@router.get("/metrics")
async def get_metrics():
    from fastapi.responses import PlainTextResponse
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
