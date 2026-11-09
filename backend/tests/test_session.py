import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.auth.profiles import ProfileRepository
from app.auth.supabase import AuthenticatedUser, AuthError
from app.config import Settings
from app.conversation.ports import Conversation, EventSink, UserContext
from app.main import create_app
from app.persistence.conversations import InMemoryConversationRepository
from app.persistence.postgrest import PostgrestClient
from app.websocket import session as session_module
from app.websocket.protocol import MAX_AUDIO_FRAME_BYTES, AssistantText, SessionStart
from app.websocket.session import WebSocketEventSink

USER_A = "7b0c3b5e-6a52-4f0e-9a63-0d8f8f1b2c11"
USER_B = "0d4f3a52-1111-4222-8333-944455556666"
TOKENS = {"token-a": USER_A, "token-b": USER_B}


class FakeAuthenticator:
    async def verify(self, token: str) -> AuthenticatedUser:
        if token not in TOKENS:
            raise AuthError
        return AuthenticatedUser(user_id=TOKENS[token])


class RecordingConversation(Conversation):
    def __init__(self, user: UserContext, start: SessionStart, conversation_id: str) -> None:
        self.user, self.start_message, self.conversation_id = user, start, conversation_id
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False

    async def start(self) -> None:
        self.calls.append(("start",))

    async def on_audio(self, chunk: bytes) -> None:
        self.calls.append(("audio", chunk))

    async def on_audio_end(self) -> None:
        self.calls.append(("audio_end",))

    async def on_text(self, text: str) -> None:
        self.calls.append(("text", text))

    async def on_interrupt(self) -> None:
        self.calls.append(("interrupt",))

    async def on_playback_started(self, generation_id: int) -> None:
        self.calls.append(("playback_started", generation_id))

    async def on_playback_finished(self, generation_id: int) -> None:
        self.calls.append(("playback_finished", generation_id))

    async def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, **settings: Any) -> None:
        self.conversations: list[RecordingConversation] = []
        self.repo = InMemoryConversationRepository()
        self.app = create_app(
            Settings(**settings),
            self._factory,
            profiles=ProfileRepository(PostgrestClient(Settings())),  # unconfigured -> defaults
            conversations=self.repo,
            authenticator=FakeAuthenticator(),
        )

    def _factory(
        self, user: UserContext, start: SessionStart, sink: EventSink, conversation_id: str
    ) -> Conversation:
        conversation = RecordingConversation(user, start, conversation_id)
        self.conversations.append(conversation)
        return conversation


def start_message(token: str = "token-a", **extra: Any) -> str:
    return json.dumps({"type": "session.start", "token": token, **extra})


def send(ws: WebSocketTestSession, type_: str, **fields: Any) -> None:
    ws.send_text(json.dumps({"type": type_, **fields}))


def sync(ws: WebSocketTestSession) -> None:
    """Server handles frames in order, so a pong proves earlier frames were routed."""
    send(ws, "ping")
    while ws.receive_json()["type"] != "pong":
        pass


def close_code(ws: WebSocketTestSession) -> int:
    while True:
        message = ws.receive()
        if message["type"] == "websocket.close":
            return int(message["code"])


@contextmanager
def ready_session(
    client: TestClient, token: str = "token-a", **extra: Any
) -> Iterator[tuple[WebSocketTestSession, dict[str, Any]]]:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(start_message(token, **extra))
        yield ws, ws.receive_json()


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_missing_session_start_closes_with_handshake_timeout() -> None:
    harness = Harness(session_handshake_timeout_s=0.1)
    with TestClient(harness.app) as client, client.websocket_connect("/ws") as ws:
        assert close_code(ws) == 4408
    assert harness.conversations == []


def test_invalid_token_closes_with_unauthenticated(harness: Harness) -> None:
    with TestClient(harness.app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(start_message("forged"))
        assert close_code(ws) == 4401
    assert harness.conversations == []


@pytest.mark.parametrize("first", ["not json", '{"type":"ping"}'])
def test_first_frame_must_be_session_start(harness: Harness, first: str) -> None:
    with TestClient(harness.app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(first)
        assert close_code(ws) == 4400


def test_ready_uses_verified_identity_never_browser_ids(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client, language="hi-IN") as (ws, ready):
            assert ready["type"] == "session.ready"
            assert ready["input_sample_rate"] == 16_000
            assert ready["output_sample_rate"] == Settings().gnani_tts_sample_rate
        conversation = harness.conversations[0]
    assert conversation.user.user_id == USER_A
    assert conversation.start_message.language == "hi-IN"
    assert conversation.conversation_id == ready["conversation_id"]
    assert conversation.calls[0] == ("start",)


def test_foreign_conversation_id_is_replaced_but_own_is_reused(harness: Harness) -> None:
    import asyncio

    foreign = asyncio.run(harness.repo.create(USER_B, "voice"))
    own = asyncio.run(harness.repo.create(USER_A, "voice"))
    with TestClient(harness.app) as client:
        with ready_session(client, conversation_id=foreign) as (_, ready):
            assert ready["conversation_id"] != foreign
        with ready_session(client, conversation_id=own) as (_, ready):
            assert ready["conversation_id"] == own


def test_every_client_message_reaches_the_matching_method(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            ws.send_bytes(b"\x01\x02")
            send(ws, "user.text", text="namaste")
            send(ws, "user.interrupt")
            send(ws, "audio.end")
            send(ws, "playback.started", generation_id=3)
            send(ws, "playback.finished", generation_id=3)
            sync(ws)
        conversation = harness.conversations[0]
    assert conversation.calls == [
        ("start",),
        ("audio", b"\x01\x02"),
        ("text", "namaste"),
        ("interrupt",),
        ("audio_end",),
        ("playback_started", 3),
        ("playback_finished", 3),
    ]


def test_session_end_closes_cleanly_and_closes_conversation(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            send(ws, "session.end")
            assert close_code(ws) == 1000
        assert harness.conversations[0].closed


def test_client_disconnect_still_closes_conversation(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client):
            pass
        assert harness.conversations[0].closed


def test_oversized_audio_frame_is_rejected_not_forwarded(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            ws.send_bytes(b"\x00" * (MAX_AUDIO_FRAME_BYTES + 1))
            assert ws.receive_json()["code"] == "bad_message"
            sync(ws)
        assert ("audio", b"\x00" * (MAX_AUDIO_FRAME_BYTES + 1)) not in harness.conversations[
            0
        ].calls
        assert not any(call[0] == "audio" for call in harness.conversations[0].calls)


def test_three_malformed_messages_close_with_bad_message(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            ws.send_text("not json")
            send(ws, "user.text", text="")
            send(ws, "nope")
            assert close_code(ws) == 4400
        assert harness.conversations[0].closed


def test_frame_flood_closes_with_rate_limited(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "FRAME_BURST", 3)
    monkeypatch.setattr(session_module, "FRAMES_PER_SECOND", 0.001)
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            for _ in range(10):
                ws.send_bytes(b"\x00\x00")
            assert close_code(ws) == 4429
        assert harness.conversations[0].closed


def test_second_session_for_same_user_supersedes_the_first(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        with ready_session(client) as (first, _), ready_session(client) as (second, _):
            assert close_code(first) == 4409
            sync(second)
        first_conversation, second_conversation = harness.conversations
        assert first_conversation.closed and second_conversation.closed


def test_different_users_do_not_supersede_each_other(harness: Harness) -> None:
    with (
        TestClient(harness.app) as client,
        ready_session(client, "token-a") as (first, _),
        ready_session(client, "token-b") as (second, _),
    ):
        sync(first)
        sync(second)
        assert client.get("/health").json()["active_sessions"] == 2


def test_session_is_closed_when_duration_cap_is_reached() -> None:
    harness = Harness(session_max_duration_s=0.2)
    with TestClient(harness.app) as client:
        with ready_session(client) as (ws, _):
            assert close_code(ws) == 1000
        assert harness.conversations[0].closed


def test_health_reports_active_sessions(harness: Harness) -> None:
    with TestClient(harness.app) as client:
        assert client.get("/health").json() == {"status": "ok", "active_sessions": 0}
        with ready_session(client):
            assert client.get("/health").json() == {"status": "ok", "active_sessions": 1}


class _BrokenSocket:
    def __init__(self) -> None:
        self.attempts = 0

    async def send_text(self, data: str) -> None:
        self.attempts += 1
        raise RuntimeError("socket closed")

    async def send_bytes(self, data: bytes) -> None:
        self.attempts += 1
        raise RuntimeError("socket closed")


async def test_sink_drops_sends_after_the_socket_fails() -> None:
    socket = _BrokenSocket()
    sink = WebSocketEventSink(socket)  # type: ignore[arg-type]
    await sink.send_event(AssistantText(generation_id=1, delta="hi"))
    await sink.send_audio(1, b"\x00")
    assert socket.attempts == 1
