"""Evolution webhook hardening tests."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zwaf.api.routes import webhook


@dataclass
class PhoneEntry:
    instance: str


class FakeWhatsApp:
    phone_numbers = [PhoneEntry(instance="livia-test")]


class FakeTenant:
    whatsapp = FakeWhatsApp()


class FakeTeam:
    _tenant = FakeTenant()

    async def process(self, message, phone, session_id, lead_id):
        raise AssertionError("background task should not run in validation tests")


def _client() -> TestClient:
    app = FastAPI()
    app.state.teams = {"livia-raiz-vital": FakeTeam()}
    app.include_router(webhook.router)
    return TestClient(app)


def test_evolution_webhook_rejects_unknown_tenant():
    app = FastAPI()
    app.state.teams = {}
    app.include_router(webhook.router)
    client = TestClient(app)

    response = client.post(
        "/missing",
        json={"event": "messages.upsert", "instance": "livia-test", "data": {}},
    )

    assert response.status_code == 404


def test_evolution_webhook_rejects_invalid_instance():
    response = _client().post(
        "/livia-raiz-vital",
        json={"event": "messages.upsert", "instance": "other-instance", "data": {}},
    )

    assert response.status_code == 403


def test_evolution_webhook_rejects_malformed_payload():
    response = _client().post(
        "/livia-raiz-vital",
        json={"event": "messages.upsert", "data": {}},
    )

    assert response.status_code == 400


def test_evolution_webhook_ignores_irrelevant_event():
    response = _client().post(
        "/livia-raiz-vital",
        json={"event": "connection.update", "instance": "livia-test", "data": {}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "connection.update"}
