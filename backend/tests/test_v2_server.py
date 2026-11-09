"""Server-level behaviour of protocol v2: reconnects, isolation between sessions, STT failure
recovery and a barge-in over a real WebSocket. Providers are the deterministic fakes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from support import ControlledSTT, Harness, final

import app.conversation.stt_link as stt_link_module
from app.auth.profiles import ProfileRepository
from app.auth.supabase import AuthenticatedUser, AuthError
from app.config import Settings
from app.conversation.ports import UserContext
from app.main import create_app
from app.persistence.conversations import InMemoryConversationRepository, StoredMessage
from app.persistence.postgrest import PostgrestClient
from app.providers.base import ChatMessage, LanguageModel, LLMChunk, ProviderError
from app.providers.fakes import FakeLanguageModel
from app.voice.state import VoiceState
from app.websocket.protocol import PipelineError, TextDelta, TurnCompleted
from app.wiring import OrynRuntime

USER = "7b0c3b5e-6a52-4f0e-9a63-0d8f8f1b2c11"
S = VoiceState


class OneUser:
    async def verify(self, token: str) -> AuthenticatedUser:
        if token != "valid":
            raise AuthError
        return AuthenticatedUser(user_id=USER)


class Stack:
    def __init__(self, tmp_path: Path) -> None:
        settings = Settings(provider_mode="fake", memory_db_path=tmp_path / "memory.db")
        self.history = InMemoryConversationRepository()
        self.runtime = OrynRuntime(settings, self.history)
        app = create_app(
            settings,
            self.runtime.create_conversation,
            profiles=ProfileRepository(PostgrestClient(Settings())),
            conversations=self.history,
            authenticator=OneUser(),
            on_shutdown=[self.runtime.aclose],
        )
        self.client = TestClient(app)

    @property
    def llm(self) -> FakeLanguageModel:
        assert isinstance(self.runtime.providers.llm, FakeLanguageModel)
        return self.runtime.providers.llm


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def send(ws: WebSocketTestSession, type_: str, **fields: Any) -> None:
    ws.send_text(json.dumps({"v": 2, "type": type_, **fields}))


def receive_until(
    ws: WebSocketTestSession, event_type: str
) -> tuple[list[dict[str, Any]], list[bytes]]:
    events: list[dict[str, Any]] = []
    frames: list[bytes] = []
    while True:
        message = ws.receive()
        if message.get("bytes") is not None:
            frames.append(message["bytes"])
            continue
        event = json.loads(message["text"])
        events.append(event)
        if event["type"] == event_type:
            return events, frames


def open_session(ws: WebSocketTestSession, **fields: Any) -> dict[str, Any]:
    send(ws, "session.start", token="valid", **fields)
    events, _ = receive_until(ws, "session.ready")
    return events[-1]


def test_reconnect_restores_history(stack: Stack) -> None:
    with stack.client:
        with stack.client.websocket_connect("/ws") as ws:
            conversation_id = open_session(ws, mode="text")["conversation_id"]
            send(ws, "user.text", text="my dog is called Bruno")
            first, _ = receive_until(ws, "turn.completed")
        assert first[-1]["turn_id"] == 1

        with stack.client.websocket_connect("/ws") as ws:  # the same call, reconnected
            ready = open_session(ws, mode="text", conversation_id=conversation_id)
            assert ready["conversation_id"] == conversation_id
            send(ws, "user.text", text="what is my dog called")
            second, _ = receive_until(ws, "turn.completed")

    sent_to_llm = [(m.role, m.content) for m in stack.llm.calls[-1]]
    assert ("user", "my dog is called Bruno") in sent_to_llm  # short-term memory survived
    assert sent_to_llm[-1] == ("user", "what is my dog called")
    assert second[-1]["turn_id"] == 2  # ids keep increasing across the reconnect
    assert second[-1]["generation_id"] == 2


def test_reconnect_to_an_empty_conversation_starts_at_one(stack: Stack) -> None:
    with stack.client, stack.client.websocket_connect("/ws") as ws:
        open_session(ws, mode="text")
        send(ws, "user.text", text="hello")
        events, _ = receive_until(ws, "turn.completed")
    assert events[-1]["turn_id"] == 1


def test_barge_in_over_the_wire_cancels_with_the_generation_id(stack: Stack) -> None:
    with stack.client, stack.client.websocket_connect("/ws") as ws:
        open_session(ws)
        send(ws, "input.speech_started")
        ws.send_bytes(bytes(640))
        send(ws, "input.speech_ended")
        started, _ = receive_until(ws, "audio.started")
        generation = started[-1]["generation_id"]

        send(ws, "turn.cancel", generation_id=generation + 99)  # stale: ignored
        send(ws, "turn.cancel", generation_id=generation)
        events, _ = receive_until(ws, "turn.cancelled")

        assert events[-1]["generation_id"] == generation
        assert events[-1]["reason"] == "client_cancel"
        after, _ = receive_until(ws, "state")
        assert after[-1]["state"] == "LISTENING"


# ---------------------------------------------------------------- engine-level isolation


class EchoLLM(LanguageModel):
    name = "echo"
    model = "echo"

    async def stream(
        self, messages: list[ChatMessage] | tuple[ChatMessage, ...], tools: Any = ()
    ) -> Any:
        await asyncio.sleep(0.001)
        yield LLMChunk(text=f"You said {messages[-1].content}. ")
        yield LLMChunk(finish_reason="stop")


async def test_ten_concurrent_sessions_are_isolated(harness: Harness) -> None:
    rigs = [harness.build(mode="text", llm=EchoLLM()) for _ in range(10)]
    for engine, _ in rigs:
        await engine.start()

    async def converse(index: int) -> None:
        engine, sink = rigs[index]
        for turn in range(3):
            await engine.on_text(f"session{index} message{turn}")
            await sink.wait_for_state(S.LISTENING, turn + 2)

    await asyncio.gather(*(converse(i) for i in range(10)))

    for index, (_, sink) in enumerate(rigs):
        replies = [e.text for e in sink.of_type(TurnCompleted)]
        assert replies == [f"You said session{index} message{t}." for t in range(3)]
        assert [e.turn_id for e in sink.of_type(TurnCompleted)] == [1, 2, 3]
        streamed = "".join(e.delta for e in sink.of_type(TextDelta))
        assert streamed.count(f"session{index} ") == 3 and streamed.count("session") == 3


# ---------------------------------------------------------------- STT failures


@pytest.fixture
def fast_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_link_module, "REOPEN_BACKOFF_S", 0.01)


@pytest.mark.usefixtures("fast_reconnect")
async def test_stt_reader_crash_reports_error_and_reopens(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()

    stt.streams[0].push(RuntimeError("parser bug"))  # not a ProviderError: used to kill the reader
    await sink.wait_for_state(S.LISTENING, 2)

    (error,) = sink.of_type(PipelineError)
    assert (error.code, error.retryable) == ("stt_error", True)
    assert "parser bug" not in error.message
    assert len(stt.streams) == 2 and stt.streams[0].closed

    await engine.on_audio(b"abc")
    await asyncio.sleep(0.02)
    assert stt.streams[1].audio == [b"abc"]  # the call is not deaf any more
    stt.streams[1].push(final("hello again"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted))


@pytest.mark.usefixtures("fast_reconnect")
async def test_a_handler_crash_does_not_leave_the_call_deaf(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()

    async def exploding(text: str, language: str | None) -> None:
        raise RuntimeError("bug in the transcript handler")

    engine._on_final = exploding  # type: ignore[method-assign]  # noqa: SLF001
    stt.streams[0].push(final("this transcript hits the bug"))
    await sink.wait_for_state(S.LISTENING, 2)  # error -> RECONNECTING -> LISTENING

    assert len(stt.streams) == 2


@pytest.mark.usefixtures("fast_reconnect")
async def test_audio_is_dropped_not_queued_while_reconnecting(harness: Harness) -> None:
    stt = ControlledSTT()
    stt.open_errors = []
    engine, sink = harness.build(stt=stt)
    await engine.start()
    stt.streams[0].push(ProviderError("prisma", "dropped", retryable=True))
    await sink.wait_for_state(S.RECONNECTING)

    for _ in range(500):  # the receive loop keeps delivering mic frames; it must never block
        await engine.on_audio(b"\x00" * 640)

    await sink.wait_for_state(S.LISTENING, 2)
    await asyncio.sleep(0.02)
    assert len(stt.streams[1].audio) <= 100  # bounded, and stale audio was not replayed


async def test_history_loader_failure_does_not_break_the_call(harness: Harness) -> None:
    async def broken() -> list[StoredMessage]:
        raise RuntimeError("postgrest down")

    engine, sink = harness.build(mode="text", load_history=broken, user=UserContext(user_id="u"))
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted))
