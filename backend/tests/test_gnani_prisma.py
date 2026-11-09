import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from app.config import Settings
from app.providers.base import ProviderError, SpeechStarted, STTEvent, TranscriptEvent
from app.providers.gnani_prisma import (
    EVENT_QUEUE_SIZE,
    FrameChunker,
    GnaniPrismaSTT,
    GnaniPrismaSTTStream,
)

Handler = Callable[[ServerConnection], Awaitable[None]]


class FakePrismaServer:
    """Local stand-in for wss://api.vachana.ai/stt/v3/stream."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.frames: list[bytes] = []
        self.commits = 0

    async def handle(self, ws: ServerConnection) -> None:
        self.headers = dict(ws.request.headers) if ws.request else {}
        await ws.send(json.dumps({"type": "connected", "config": {"chunk_size": 512}}))
        async for message in ws:
            if isinstance(message, bytes):
                self.frames.append(message)
                if len(self.frames) == 1:
                    await ws.send(json.dumps({"type": "speech_start"}))
            elif json.loads(message) == {"type": "commit"}:
                self.commits += 1
                await ws.send(json.dumps({"type": "processing"}))
                transcript = {
                    "type": "transcript",
                    "text": "namaste",
                    "detected_language_code": "hi-IN",
                }
                await ws.send(json.dumps(transcript))


@asynccontextmanager
async def prisma(handler: Handler, *, reject: HTTPStatus | None = None) -> AsyncIterator[Settings]:
    def process_request(connection: Any, request: Any) -> Any:
        return connection.respond(reject, "rejected") if reject else None

    async with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]
        yield Settings(
            _env_file=None,  # type: ignore[call-arg]
            gnani_api_key="test-key",  # type: ignore[arg-type]
            gnani_stt_ws_url=f"ws://127.0.0.1:{port}",
        )


async def take(events: AsyncIterator[STTEvent], n: int) -> list[STTEvent]:
    async with asyncio.timeout(3):
        return [await anext(events) for _ in range(n)]


# ---------------------------------------------------------------- FrameChunker


def test_chunker_emits_only_full_frames_and_keeps_remainder() -> None:
    chunker = FrameChunker(size=4)
    assert chunker.push(b"abc") == []
    assert chunker.push(b"defghij") == [b"abcd", b"efgh"]
    assert chunker.push(b"k") == []  # "ijk" pending
    assert chunker.flush() == b"ijk\x00"
    assert chunker.flush() is None


def test_chunker_splits_one_large_push_into_exact_frames() -> None:
    frames = FrameChunker(size=1024).push(bytes(3000))
    assert [len(f) for f in frames] == [1024, 1024]


# ---------------------------------------------------------------- stream behaviour


async def test_sends_config_headers_exact_frames_and_maps_events() -> None:
    server = FakePrismaServer()
    async with prisma(server.handle) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            for piece in (bytes(700), bytes(900), bytes(500)):  # 2100 bytes, odd boundaries
                await stream.send_audio(piece)
            await stream.finish()  # the 52-byte remainder is padded to a full frame, then commit
            events = await take(stream.events(), 2)
        finally:
            await stream.close()

    assert events == [SpeechStarted(), TranscriptEvent("namaste", is_final=True, language="hi-IN")]
    assert [len(f) for f in server.frames] == [1024, 1024, 1024]
    assert server.commits == 1
    assert server.headers["x-api-key-id"] == "test-key"
    assert server.headers["language_code"] == "hi-IN"
    assert server.headers["model"] == "prisma-v3.0"
    assert server.headers["x-sample-rate"] == "16000"
    assert server.headers["format"] == "codemix"
    assert server.headers["x-idle-timeout-sec"] == "900"


async def test_finish_without_pending_audio_only_commits() -> None:
    server = FakePrismaServer()
    async with prisma(server.handle) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("en-IN")
        try:
            await stream.send_audio(bytes(1024))
            await stream.finish()
            await take(stream.events(), 2)
        finally:
            await stream.close()
    assert len(server.frames) == 1 and server.commits == 1


def error_then_close(code: int) -> Handler:
    async def handler(ws: ServerConnection) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "message": "boom"}))
        await ws.close(code, "bye")

    return handler


@pytest.mark.parametrize(("code", "retryable"), [(1008, False), (1001, True), (1011, True)])
async def test_error_message_and_close_code_map_to_provider_error(
    code: int, retryable: bool
) -> None:
    async with prisma(error_then_close(code)) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            await stream.send_audio(bytes(1024))
            with pytest.raises(ProviderError) as info:
                await take(stream.events(), 1)
        finally:
            await stream.close()
    assert info.value.retryable is retryable


async def test_session_cap_close_without_error_message_is_retryable() -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.close(1001, "session limit")

    async with prisma(handler) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            with pytest.raises(ProviderError) as info:
                await take(stream.events(), 1)
        finally:
            await stream.close()
    assert info.value.retryable is True


async def test_connection_loss_is_retryable() -> None:
    async def handler(ws: ServerConnection) -> None:
        ws.transport.abort()

    async with prisma(handler) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            with pytest.raises(ProviderError) as info:
                await take(stream.events(), 1)
        finally:
            await stream.close()
    assert info.value.retryable is True


async def test_sending_after_the_server_closed_raises_provider_error() -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.close(1008, "bad header")

    async with prisma(handler) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            await asyncio.sleep(0.1)
            with pytest.raises(ProviderError) as info:
                await stream.send_audio(bytes(1024))
        finally:
            await stream.close()
    assert info.value.retryable is False


async def test_clean_server_close_ends_the_event_stream() -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.close(1000)

    async with prisma(handler) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        try:
            async with asyncio.timeout(3):
                assert [e async for e in stream.events()] == []
        finally:
            await stream.close()


# ---------------------------------------------------------------- connect failures


async def test_rejected_key_is_not_retryable() -> None:
    async with prisma(FakePrismaServer().handle, reject=HTTPStatus.UNAUTHORIZED) as settings:
        with pytest.raises(ProviderError) as info:
            await GnaniPrismaSTT(settings).open_stream("hi-IN")
    assert info.value.retryable is False


async def test_unavailable_gateway_is_retryable() -> None:
    async with prisma(FakePrismaServer().handle, reject=HTTPStatus.SERVICE_UNAVAILABLE) as settings:
        with pytest.raises(ProviderError) as info:
            await GnaniPrismaSTT(settings).open_stream("hi-IN")
    assert info.value.retryable is True


async def test_refused_connection_is_retryable() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        gnani_api_key="k",  # type: ignore[arg-type]
        gnani_stt_ws_url="ws://127.0.0.1:1",
    )
    with pytest.raises(ProviderError) as info:
        await GnaniPrismaSTT(settings).open_stream("hi-IN")
    assert info.value.retryable is True


# ---------------------------------------------------------------- close


async def test_close_is_idempotent_and_terminates_events() -> None:
    async with prisma(FakePrismaServer().handle) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("hi-IN")
        await stream.close()
        await stream.close()
        async with asyncio.timeout(3):
            assert [e async for e in stream.events()] == []


# ---------------------------------------------------------------- robustness


async def test_payloads_that_are_not_json_objects_are_ignored() -> None:
    async def handler(ws: ServerConnection) -> None:
        await ws.send("[1, 2, 3]")
        await ws.send('"just a string"')
        await ws.send(json.dumps({"type": "transcript", "text": "still works"}))
        await ws.wait_closed()

    async with prisma(handler) as settings:
        stream = await GnaniPrismaSTT(settings).open_stream("en-IN")
        try:
            (event,) = await take(stream.events(), 1)
            assert isinstance(event, TranscriptEvent) and event.text == "still works"
        finally:
            await stream.close()


class _FloodSocket:
    """A websocket that delivers far more transcripts than the consumer will read."""

    close_code = None
    close_reason = None

    def __init__(self, count: int) -> None:
        self._count = count
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[str]:
        for i in range(self._count):
            yield json.dumps({"type": "transcript", "text": f"t{i}"})
        await asyncio.sleep(60)

    async def close(self) -> None:
        self.closed = True


async def test_event_queue_is_bounded_when_nobody_reads() -> None:
    socket = _FloodSocket(EVENT_QUEUE_SIZE * 3)
    stream = GnaniPrismaSTTStream(socket)  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    assert stream._events.qsize() == EVENT_QUEUE_SIZE  # noqa: SLF001 - full, and no fuller

    await stream.close()  # even with a full queue, close terminates events()
    async with asyncio.timeout(3):
        assert len([e async for e in stream.events()]) <= EVENT_QUEUE_SIZE


class _SlowToStopSocket:
    """A websocket whose reader needs a moment to wind down after being cancelled."""

    def __init__(self) -> None:
        self.closed = False
        self.close_code = None
        self.close_reason = None

    def __aiter__(self) -> "_SlowToStopSocket":
        return self

    async def __anext__(self) -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            raise
        return ""

    async def close(self) -> None:
        self.closed = True


async def test_close_does_not_swallow_the_cancellation_of_its_caller() -> None:
    socket = _SlowToStopSocket()
    stream = GnaniPrismaSTTStream(socket)  # type: ignore[arg-type]
    closing = asyncio.create_task(stream.close())
    await asyncio.sleep(0.05)  # close() is now waiting for the reader to stop

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert socket.closed  # and the socket was still released
