"""The conversation's connection to the speech-to-text provider.

Owns the live STT stream and keeps it alive: audio comes in through a bounded, non-blocking
queue (the WebSocket receive loop never waits on the provider), a pump task writes it to the
provider in order, a reader task hands provider events to the engine, and a supervisor task
reopens the stream when it fails or ends. While the stream is down, audio is dropped - it is
real time and stale a second later - and the engine is told through `on_status`.
Nothing here holds a lock across a provider call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum

from app.providers.base import (
    ProviderError,
    SpeechToText,
    SpeechToTextStream,
    STTEvent,
)

log = logging.getLogger(__name__)

INGRESS_FRAMES = 100  # ~2 s of 20 ms frames; beyond that the oldest audio is dropped
STABLE_AFTER_S = 5.0  # a stream that lived this long resets the failure count
MAX_QUICK_FAILURES = 3
REOPEN_BACKOFF_S = 0.5
_COMMIT = object()


class LinkStatus(StrEnum):
    RECONNECTING = "reconnecting"
    RESTORED = "restored"
    FAILED = "failed"


EventHandler = Callable[[STTEvent], Awaitable[None]]
StatusHandler = Callable[[LinkStatus, ProviderError | None], Awaitable[None]]


class SttLink:
    def __init__(
        self,
        stt: SpeechToText,
        language: str,
        *,
        on_event: EventHandler,
        on_status: StatusHandler,
    ) -> None:
        self._stt = stt
        self._language = language
        self._on_event = on_event
        self._on_status = on_status
        self._ingress = asyncio.Queue[object](INGRESS_FRAMES)
        self._stream: SpeechToTextStream | None = None
        self._connected = False
        self._closed = False
        self._supervisor: asyncio.Task[None] | None = None
        self.dropped_frames = 0

    async def start(self) -> None:
        """Open the first stream. Raises ProviderError so the handshake can fail cleanly."""
        self._stream = await self._stt.open_stream(self._language)
        self._connected = True
        self._supervisor = asyncio.create_task(self._supervise(self._stream), name="stt-link")

    def push_audio(self, chunk: bytes) -> None:
        self._enqueue(chunk)

    def commit(self) -> None:
        """The user stopped speaking: ask the provider to finish the utterance now. Ordered
        after every frame already pushed."""
        self._enqueue(_COMMIT)

    async def close(self) -> None:
        self._closed = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.wait({self._supervisor})
        if self._stream is not None:
            await self._close_stream(self._stream)
            self._stream = None

    # ------------------------------------------------------------ ingress

    def _enqueue(self, item: object) -> None:
        if not self._connected:
            self.dropped_frames += item is not _COMMIT
            return
        if self._ingress.full():
            self._ingress.get_nowait()
            self.dropped_frames += 1
        self._ingress.put_nowait(item)

    # ------------------------------------------------------------ supervision

    async def _supervise(self, stream: SpeechToTextStream | None) -> None:
        loop = asyncio.get_running_loop()
        quick_failures = 0
        while stream is not None:
            started = loop.time()
            error = await self._serve(stream)
            if self._closed:
                return
            lifetime = loop.time() - started
            broke_early = lifetime <= STABLE_AFTER_S and (error is not None or lifetime < 1.0)
            quick_failures = quick_failures + 1 if broke_early else 0
            if (error is not None and not error.retryable) or quick_failures > MAX_QUICK_FAILURES:
                await self._on_status(LinkStatus.FAILED, error)
                return
            if error is not None:
                await self._on_status(LinkStatus.RECONNECTING, error)
            stream = await self._reopen()
            if stream is None:
                return
            if error is not None:
                await self._on_status(LinkStatus.RESTORED, None)

    async def _reopen(self) -> SpeechToTextStream | None:
        await asyncio.sleep(REOPEN_BACKOFF_S)
        try:
            self._stream = await self._stt.open_stream(self._language)
            self._connected = True
        except ProviderError as exc:
            log.warning("stt reopen failed", extra={"provider": exc.provider})
            await self._on_status(LinkStatus.FAILED, exc)
            return None
        return self._stream

    async def _serve(self, stream: SpeechToTextStream) -> ProviderError | None:
        """Run one stream until it ends or fails; the error, or None for a clean end."""
        self._stream = stream
        reader = asyncio.create_task(self._read(stream), name="stt-reader")
        pump = asyncio.create_task(self._pump(stream), name="stt-pump")
        try:
            done, _ = await asyncio.wait({reader, pump}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._connected = False
            self._drain_ingress()  # stale audio must not leak into the next stream
            for task in (reader, pump):
                task.cancel()
            await asyncio.wait({reader, pump})
        await self._close_stream(stream)
        return next((error for task in done if (error := _failure(task))), None)

    async def _read(self, stream: SpeechToTextStream) -> None:
        async for event in stream.events():
            await self._on_event(event)

    async def _pump(self, stream: SpeechToTextStream) -> None:
        while True:
            item = await self._ingress.get()
            if item is _COMMIT:
                await stream.finish()
            else:
                assert isinstance(item, bytes)
                await stream.send_audio(item)

    def _drain_ingress(self) -> None:
        while not self._ingress.empty():
            self._ingress.get_nowait()

    @staticmethod
    async def _close_stream(stream: SpeechToTextStream) -> None:
        try:
            await stream.close()
        except Exception:
            log.exception("closing stt stream failed")


def _failure(task: asyncio.Task[None]) -> ProviderError | None:
    """What ended a finished task: a ProviderError as is, any other exception wrapped
    (the call must survive a bug in a handler), None for a clean end."""
    with contextlib.suppress(asyncio.CancelledError):
        exc = task.exception()
        if isinstance(exc, ProviderError):
            return exc
        if exc is not None:
            log.error("stt task crashed", exc_info=exc)
            return ProviderError("stt", f"internal error: {exc}", retryable=True)
    return None
