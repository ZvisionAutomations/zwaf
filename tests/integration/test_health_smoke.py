"""Integration smoke tests for the public health endpoint."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zwaf.api.routes.health import router


def test_health_endpoint_returns_ok():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "zwaf", "version": "1.0.0"}
