"""Evolution webhook hardening tests."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from zwaf.audio.transcription import AudioContent, TranscriptionResult
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

    async def process(self, message, phone, session_id, lead_id, push_name=""):
        raise AssertionError("background task should not run in validation tests")

    async def send_response(self, phone, text, session_id):
        raise AssertionError("background task should not run in validation tests")


class ProcessingTeam:
    def __init__(self):
        self.process_calls = []
        self.sent_messages = []

    async def process(self, message, phone, session_id, lead_id, push_name=""):
        self.process_calls.append(
            {
                "message": message,
                "phone": phone,
                "session_id": session_id,
                "lead_id": lead_id,
                "push_name": push_name,
            }
        )
        return type(
            "Response",
            (),
            {
                "response": "ok",
                "agent_used": "vendedor",
                "latency_ms": 10.0,
            },
        )()

    async def send_response(self, phone, text, session_id):
        self.sent_messages.append(
            {"phone": phone, "text": text, "session_id": session_id}
        )


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


def test_evolution_webhook_ignores_chats_update_with_list_data():
    response = _client().post(
        "/livia-raiz-vital",
        json={
            "event": "chats.update",
            "instance": "livia-test",
            "data": [{"remoteJid": "20444665122875@lid"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event": "chats.update"}


def test_extract_audio_message_detects_voice_note():
    phone, message, key, push_name = webhook._extract_audio_message(
        {
            "data": {
                "key": {
                    "remoteJid": "5511999990001@s.whatsapp.net",
                    "fromMe": False,
                    "id": "msg-1",
                },
                "pushName": "Lead",
                "message": {"audioMessage": {"mimetype": "audio/ogg", "ptt": True}},
            }
        }
    )

    assert phone == "5511999990001"
    assert message["audioMessage"]["ptt"] is True
    assert key["id"] == "msg-1"
    assert push_name == "Lead"


def test_extract_audio_message_ignores_from_me():
    phone, message, key, push_name = webhook._extract_audio_message(
        {
            "data": {
                "key": {"remoteJid": "5511999990001@s.whatsapp.net", "fromMe": True},
                "message": {"audioMessage": {"mimetype": "audio/ogg", "ptt": True}},
            }
        }
    )

    assert (phone, message, key, push_name) == ("", {}, {}, "")


def test_audio_webhook_processes_before_response(monkeypatch):
    app = FastAPI()
    app.state.teams = {"livia-raiz-vital": ProcessingTeam()}
    app.include_router(webhook.router)
    client = TestClient(app)
    calls = []

    async def fake_process_audio_and_respond(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(webhook, "_process_audio_and_respond", fake_process_audio_and_respond)

    response = client.post(
        "/livia-raiz-vital",
        json={
            "event": "messages.upsert",
            "instance": "livia-test",
            "data": {
                "key": {
                    "remoteJid": "5511999990001@s.whatsapp.net",
                    "fromMe": False,
                    "id": "msg-1",
                },
                "pushName": "Lead",
                "message": {"audioMessage": {"mimetype": "audio/ogg", "ptt": True}},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert calls
    assert calls[0]["phone"] == "5511999990001"


@pytest.mark.asyncio
async def test_process_audio_routes_transcribed_text(monkeypatch, caplog):
    team = ProcessingTeam()

    async def fake_load_audio_content(**kwargs):
        return AudioContent(bytes_data=b"audio")

    async def fake_transcribe_audio(audio):
        return TranscriptionResult(ok=True, text="quero comprar new woman")

    monkeypatch.setattr("zwaf.audio.transcription.load_audio_content", fake_load_audio_content)
    monkeypatch.setattr("zwaf.audio.transcription.transcribe_audio", fake_transcribe_audio)

    with caplog.at_level("INFO", logger="zwaf.api.webhook"):
        await webhook._process_audio_and_respond(
            team=team,
            audio_message={"audioMessage": {"mimetype": "audio/ogg", "ptt": True}},
            message_key={"id": "msg-1"},
            phone="5511999990001",
            session_id="sess-1",
            lead_id="lead-1",
            tenant_id="livia-raiz-vital",
            instance="livia-test",
        )

    assert team.process_calls[0]["message"] == "quero comprar new woman"
    assert team.sent_messages[0]["text"] == "ok"
    messages = "\n".join(record.message for record in caplog.records)
    assert "audio_loaded" in messages
    assert "audio_transcribed" in messages
    assert "audio_agent_processed" in messages
    assert "audio_send_started" in messages
    assert "audio_send_success" in messages
    assert "quero comprar new woman" not in messages


@pytest.mark.asyncio
async def test_process_audio_failure_sends_fallback_without_agent(monkeypatch):
    team = ProcessingTeam()

    async def fake_load_audio_content(**kwargs):
        return TranscriptionResult(
            ok=False,
            code="provider_disabled",
            fallback_message="Pode me mandar por texto?",
        )

    monkeypatch.setattr("zwaf.audio.transcription.load_audio_content", fake_load_audio_content)

    await webhook._process_audio_and_respond(
        team=team,
        audio_message={"audioMessage": {"mimetype": "audio/ogg", "ptt": True}},
        message_key={"id": "msg-1"},
        phone="5511999990001",
        session_id="sess-1",
        lead_id="lead-1",
        tenant_id="livia-raiz-vital",
        instance="livia-test",
    )

    assert team.process_calls == []
    assert team.sent_messages == [
        {
            "phone": "5511999990001",
            "text": "Pode me mandar por texto?",
            "session_id": "sess-1",
        }
    ]


# ---------------------------------------------------------------------------
# story-068: extracao do pushName no inbound de texto + fallback CTWA/@lid
# ---------------------------------------------------------------------------


def test_extract_message_captures_push_name():
    phone, text, push_name = webhook._extract_message(
        {
            "data": {
                "key": {"remoteJid": "5511999990001@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "ola"},
                "pushName": "Maria Silva",
            }
        }
    )
    assert phone == "5511999990001"
    assert text == "ola"
    assert push_name == "Maria Silva"


def test_extract_message_push_name_empty_when_absent_ctwa():
    # Lead CTWA/@lid de anuncio: pushName ausente -> "" (NUNCA o telefone).
    phone, text, push_name = webhook._extract_message(
        {
            "data": {
                "key": {"remoteJid": "5511999990001@lid", "fromMe": False},
                "message": {"conversation": "ola"},
            }
        }
    )
    assert phone == "5511999990001"
    assert push_name == ""


@pytest.mark.asyncio
async def test_process_and_respond_threads_push_name_to_team():
    team = ProcessingTeam()
    await webhook._process_and_respond(
        team,
        "quero comprar",
        "5511999990001",
        "sess-1",
        "lead-1",
        "livia-raiz-vital",
        "Joao Pedro",
    )
    assert team.process_calls[0]["push_name"] == "Joao Pedro"
    assert team.sent_messages[0]["text"] == "ok"
