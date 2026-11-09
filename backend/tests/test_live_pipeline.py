"""End to end through the real stack - WebSocket session, runtime wiring, engine, memory and
tools - with only the network providers replaced by fakes (PROVIDER_MODE=fake)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.auth.profiles import ProfileRepository
from app.auth.supabase import AuthenticatedUser, AuthError
from app.config import Settings
from app.main import build_app, create_app
from app.persistence.conversations import InMemoryConversationRepository
from app.persistence.postgrest import PostgrestClient
from app.websocket.protocol import AUDIO_HEADER
from app.wiring import OrynRuntime

USER = "7b0c3b5e-6a52-4f0e-9a63-0d8f8f1b2c11"


class OneUserAuthenticator:
    async def verify(self, token: str) -> AuthenticatedUser:
        if token != "valid":
            raise AuthError
        return AuthenticatedUser(user_id=USER)


def fake_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(provider_mode="fake", memory_db_path=tmp_path / "memory.db", **overrides)


@pytest.fixture
def stack(tmp_path: Path) -> tuple[TestClient, InMemoryConversationRepository]:
    settings = fake_settings(tmp_path)
    history = InMemoryConversationRepository()
    runtime = OrynRuntime(settings, history)
    app = create_app(
        settings,
        runtime.create_conversation,
        profiles=ProfileRepository(PostgrestClient(Settings())),  # unconfigured -> defaults
        conversations=history,
        authenticator=OneUserAuthenticator(),
        on_shutdown=[runtime.aclose],
    )
    return TestClient(app), history


def receive_until(ws: WebSocketTestSession, event_type: str) -> tuple[list[dict[str, Any]], int]:
    """JSON events up to and including `event_type`, plus how many audio frames arrived."""
    events: list[dict[str, Any]] = []
    audio_frames = 0
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            audio_frames += 1
            continue
        event = json.loads(message["text"])
        events.append(event)
        if event["type"] == event_type:
            return events, audio_frames


def test_spoken_turn_flows_from_microphone_to_speaker(
    stack: tuple[TestClient, InMemoryConversationRepository],
) -> None:
    client, history = stack
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "token": "valid", "language": "hi-IN"}))
        ready, _ = receive_until(ws, "session.ready")

        ws.send_bytes(bytes(640))  # 20 ms of 16 kHz PCM
        ws.send_text(json.dumps({"type": "audio.end"}))
        events, audio_frames = receive_until(ws, "assistant.done")

        types = [e["type"] for e in events]
        assert types.index("transcript.final") < types.index("assistant.audio.start")
        assert audio_frames > 0
        done = events[-1]
        states = [e["state"] for e in ready + events if e["type"] == "state"]
        assert states == ["LISTENING", "PROCESSING", "GENERATING", "SPEAKING"]

        ws.send_text(
            json.dumps({"type": "playback.finished", "generation_id": done["generation_id"]})
        )
        after, _ = receive_until(ws, "state")
        assert after[-1] == {"type": "state", "state": "LISTENING"}
        assert [e["type"] for e in after] == ["metrics", "state"]

    conversation_id = ready[-1]["conversation_id"]
    stored = history._messages[conversation_id]  # noqa: SLF001 - test-only peek
    assert [m.role for m in stored] == ["user", "assistant"]


def test_audio_frames_carry_the_generation_of_the_reply(
    stack: tuple[TestClient, InMemoryConversationRepository],
) -> None:
    client, _ = stack
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "token": "valid"}))
        receive_until(ws, "session.ready")
        ws.send_bytes(bytes(640))
        ws.send_text(json.dumps({"type": "audio.end"}))

        generation: int | None = None
        while True:
            message = ws.receive()
            if (frame := message.get("bytes")) is not None:
                assert AUDIO_HEADER.unpack(frame[:4]) == (generation,)
                break
            event = json.loads(message["text"])
            if event["type"] == "assistant.audio.start":
                generation = event["generation_id"]


def test_typed_message_gets_a_text_only_reply(
    stack: tuple[TestClient, InMemoryConversationRepository],
) -> None:
    client, _ = stack
    with client, client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "session.start", "token": "valid", "mode": "text"}))
        receive_until(ws, "session.ready")
        ws.send_text(json.dumps({"type": "user.text", "text": "hello"}))
        events, audio_frames = receive_until(ws, "assistant.done")

    assert audio_frames == 0
    assert any(e["type"] == "assistant.text" for e in events)


def test_production_app_serves_health_metrics_and_guards_memories(tmp_path: Path) -> None:
    with TestClient(build_app(fake_settings(tmp_path))) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert isinstance(client.get("/metrics").json(), dict)
        assert client.get("/memories").status_code == 401


def test_production_app_fails_fast_without_live_keys(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="GNANI_API_KEY"):
        build_app(Settings(provider_mode="live", memory_db_path=tmp_path / "memory.db"))
