"""Gnani Timbre text-to-speech over one persistent WebSocket.

Why it exists: SSE pays HTTP framing per sentence; here one socket serves requests back to back,
and `warm_up()` pays the 0.4-4 s handshake before the first reply, not during it.

Verified live 2026-11-09 (see docs/context/providers.md):
- One socket serves sequential requests. Config goes in the JSON body, only `X-API-Key-ID` is a
  header. A request is `start`, `audio`* (base64 raw PCM), then TWO `complete` frames: one with
  `data.is_final` and a trailer carrying `message`/`request_id`. The trailer is skipped by only
  honouring `complete` after this request's `start`.
- There is no cancel message. Closing gracefully mid-synthesis hangs for `close_timeout` (the
  server is busy), so the transport is aborted instead; the account slot is then free at once
  (an immediate reconnect and new request got no 429).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
from websockets.protocol import State

from app.config import Settings
from app.providers.base import AudioFormat, ProviderError, TextToSpeech
from app.providers.gnani_timbre import (
    NAME,
    RETRY_BACKOFF_S,
    SampleAligner,
    account_slots,
    request_body,
    strip_wav_header,
)
from app.providers.http import RETRYABLE_STATUSES

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0  # handshakes took 0.4-4.3 s live
FRAME_TIMEOUT_S = 15.0  # longest silence tolerated inside one request
PING_INTERVAL_S = 20.0  # the values Gnani's own SDK uses
PING_TIMEOUT_S = 20.0
CLOSE_TIMEOUT_S = 2.0
_BUSY_MARKER = "active request"  # "Only 1 active request is allowed at a time ..." (a 429)


class _ServerError(ProviderError):
    """An `error` frame; the docs say the connection stays open after these."""


class GnaniTimbreRealtimeTTS(TextToSpeech):
    name = NAME

    def __init__(
        self,
        settings: Settings,
        *,
        backoff: Sequence[float] = RETRY_BACKOFF_S,
        slots: asyncio.Semaphore | None = None,
    ) -> None:
        if settings.gnani_api_key is None:
            raise ValueError("GNANI_API_KEY is required for Gnani Timbre")
        self._settings = settings
        self._backoff = tuple(backoff)
        self._slots = slots or account_slots(settings)
        self._headers = {"X-API-Key-ID": settings.gnani_api_key.get_secret_value()}
        self.output_format = AudioFormat(sample_rate=settings.gnani_tts_sample_rate)
        self._ws: ClientConnection | None = None
        self._connect_lock = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()
        self._closed = False

    async def warm_up(self) -> None:
        """Open the socket now so the first reply does not pay the handshake."""
        await self._connection()

    async def aclose(self) -> None:
        self._closed = True
        for task in tuple(self._background):
            task.cancel()
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(TimeoutError, ConnectionClosed, OSError):
                await asyncio.wait_for(ws.close(), CLOSE_TIMEOUT_S)
            ws.transport.abort()
        for task in tuple(self._background):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        body = request_body(self._settings, self.output_format, text, language, voice)
        for delay in (*self._backoff, None):
            started = False
            try:
                # The slot is held per attempt, never across a retry sleep. aclosing: a cancelled
                # consumer must drop the socket at once, not whenever the generator is collected.
                async with self._slots, contextlib.aclosing(self._attempt(body)) as chunks:
                    async for pcm in chunks:
                        started = True
                        yield pcm
                return
            except ProviderError as exc:
                if started or not exc.retryable or delay is None:
                    raise
                log.warning("tts ws retry", extra={"delay_s": delay, "error": str(exc)})
            await asyncio.sleep(delay)

    async def _attempt(self, body: dict[str, Any]) -> AsyncGenerator[bytes]:
        ws = await self._connection()
        keep = False  # only a finished request (or a recoverable error frame) leaves a clean socket
        try:
            try:
                await ws.send(json.dumps(body))
                async for pcm in self._read_request(ws):
                    yield pcm
            except ProviderError as exc:
                keep = isinstance(exc, _ServerError)
                raise
            else:
                keep = True
        except ConnectionClosed as exc:
            raise ProviderError(NAME, f"connection lost: {exc}", retryable=True) from exc
        finally:
            if not keep:
                self._discard(ws)

    async def _read_request(self, ws: ClientConnection) -> AsyncGenerator[bytes]:
        aligner = SampleAligner()
        started = False  # `complete` frames before our `start` belong to the previous request
        while True:
            try:
                async with asyncio.timeout(FRAME_TIMEOUT_S):
                    raw = await ws.recv()
            except TimeoutError as exc:
                raise ProviderError(NAME, "timed out waiting for audio", retryable=True) from exc
            if isinstance(raw, bytes):  # not seen live, but Gnani's SDK accepts it
                started = True
                if pcm := aligner.push(strip_wav_header(raw)):
                    yield pcm
                continue
            try:
                message: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProviderError(NAME, f"malformed frame: {exc}") from exc
            match message.get("type"):
                case "start":
                    started = True
                case "audio":
                    started = True
                    encoded = (message.get("data") or {}).get("audio")
                    if encoded and (
                        pcm := aligner.push(strip_wav_header(base64.b64decode(encoded)))
                    ):
                        yield pcm
                case "complete" if started:
                    return
                case "error":
                    detail = str(message.get("message", "stream error"))
                    # The busy-account 429 is transient: the previous synthesis is winding down.
                    raise _ServerError(NAME, detail, retryable=_BUSY_MARKER in detail.lower())

    async def _connection(self) -> ClientConnection:
        async with self._connect_lock:
            if self._closed:
                raise ProviderError(NAME, "transport is closed")
            if self._ws is None or self._ws.state is not State.OPEN:
                self._ws = await self._open()
            return self._ws

    async def _open(self) -> ClientConnection:
        try:
            return await connect(
                self._settings.gnani_tts_ws_url,
                additional_headers=self._headers,
                open_timeout=CONNECT_TIMEOUT_S,
                ping_interval=PING_INTERVAL_S,
                ping_timeout=PING_TIMEOUT_S,
                close_timeout=CLOSE_TIMEOUT_S,
            )
        except InvalidStatus as exc:  # 401 on a bad key: not retryable
            status = exc.response.status_code
            raise ProviderError(
                NAME, f"handshake rejected: HTTP {status}", retryable=status in RETRYABLE_STATUSES
            ) from exc
        except (InvalidHandshake, TimeoutError, OSError) as exc:
            raise ProviderError(NAME, f"cannot connect: {exc}", retryable=True) from exc

    def _discard(self, ws: ClientConnection) -> None:
        """Drop a socket that may still be synthesising and reconnect in the background.

        Synchronous on purpose (it runs while being cancelled). A graceful close would wait out
        `close_timeout` for a server that is mid-synthesis, so the transport is aborted.
        """
        if self._ws is ws:
            self._ws = None
        ws.transport.abort()
        if self._closed:
            return
        task = asyncio.create_task(self._reconnect(), name="timbre-reconnect")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _reconnect(self) -> None:
        try:
            await self._connection()
        except ProviderError as exc:  # the next synthesize() retries and reports it
            log.warning("tts ws reconnect failed", extra={"error": str(exc)})
