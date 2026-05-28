"""FastAPI app factory — ZWAF API multi-tenant."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from zwaf.api.limiter import limiter
from zwaf.api.middleware.auth import APIKeyMiddleware
from zwaf.api.routes import health, webhook, payment_webhook
from zwaf.core.team import build_team
from zwaf.core.tenant import TenantConfig, TenantLoadError
from zwaf.reporting.error_handler import notify_critical_error, setup_critical_error_handler
from zwaf.reporting.scheduler import register_daily_report_scheduler

logger = logging.getLogger("zwaf.api")

_ENV = os.getenv("ENV", "development")
_TENANTS_ROOT = Path(__file__).parent.parent.parent.parent / "tenants"


def _discover_tenants() -> list[str]:
    """Descobre tenant IDs no diretorio tenants/ (qualquer pasta com config.json)."""
    if not _TENANTS_ROOT.exists():
        return []
    return [
        d.name
        for d in _TENANTS_ROOT.iterdir()
        if d.is_dir() and (d / "config.json").exists() and not d.name.startswith("_")
    ]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: carrega todos os tenants e inicializa ZWAFTeams."""
    db_url = os.getenv("DATABASE_URL", "")

    tenant_ids = (
        os.getenv("ZWAF_TENANTS", "").split(",")
        if os.getenv("ZWAF_TENANTS")
        else _discover_tenants()
    )
    tenant_ids = [t.strip() for t in tenant_ids if t.strip()]

    if not tenant_ids:
        logger.warning("No tenants configured — check tenants/ directory or ZWAF_TENANTS env var")

    teams = {}
    for tenant_id in tenant_ids:
        try:
            config = TenantConfig.load(tenant_id, tenants_root=_TENANTS_ROOT)
            team = build_team(config, db_url=db_url)
            teams[tenant_id] = team
            logger.info("Tenant loaded: %s (agents: %s)", tenant_id, config.agents_enabled)
        except TenantLoadError as e:
            logger.error("Failed to load tenant '%s': %s", tenant_id, e)
        except Exception as e:
            logger.error("Unexpected error loading tenant '%s': %s", tenant_id, e)

    app.state.teams = teams

    critical_whatsapp_tool = None
    for tenant_id, team in teams.items():
        whatsapp_tool = getattr(team, "_whatsapp", None)
        if critical_whatsapp_tool is None and whatsapp_tool is not None:
            critical_whatsapp_tool = whatsapp_tool
        register_daily_report_scheduler(
            agno_app=app,
            db_url=db_url,
            tenant_id=tenant_id,
            whatsapp_tool=whatsapp_tool,
        )

    setup_critical_error_handler(critical_whatsapp_tool)
    app.state.critical_error_whatsapp_tool = critical_whatsapp_tool
    logger.info("ZWAF started with %d tenant(s): %s", len(teams), list(teams.keys()))

    yield

    # Shutdown: parar scheduler de relatorio diario
    for scheduler in getattr(app.state, "daily_report_schedulers", []):
        try:
            if scheduler.running:
                scheduler.shutdown(wait=False)
        except Exception:
            pass

    # Shutdown: parar schedulers de fidelizacao
    for tenant_id, team in teams.items():
        try:
            scheduler = getattr(team, "_fidelizacao_scheduler", None)
            if scheduler:
                scheduler.stop()
        except Exception:
            pass

    logger.info("ZWAF shutting down")


# CORS
_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
if not _cors_origins_raw:
    if _ENV == "production":
        raise RuntimeError("CORS_ORIGINS must be set in production")
    _cors_origins = ["*"]
else:
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app = FastAPI(
    title="ZWAF API",
    description="Zvision WhatsApp Agent Framework — multi-tenant B2C",
    version="1.0.0",
    docs_url="/docs" if _ENV != "production" else None,
    redoc_url="/redoc" if _ENV != "production" else None,
    openapi_url="/openapi.json" if _ENV != "production" else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["System"])
app.include_router(webhook.router, prefix="/v1/webhook", tags=["Webhook"])
app.include_router(payment_webhook.router, prefix="/v1/webhook", tags=["Payments"])


@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception) -> JSONResponse:
    whatsapp_tool = getattr(request.app.state, "critical_error_whatsapp_tool", None)
    await notify_critical_error(whatsapp_tool, exc)
    logger.error("Unhandled exception: %s path=%s", str(exc), str(request.url))
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno. Tente novamente em instantes."},
    )
