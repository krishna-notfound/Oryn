"""Gnani Prisma realtime speech-to-text over WebSocket.

Browsers cannot set upgrade headers, so the backend proxies: PCM from the browser is
re-chunked into the exact 1024-byte frames Prisma demands and forwarded, and Prisma's JSON
messages become `SpeechStarted` / final `TranscriptEvent`s. Prisma has no partial transcripts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from app.config import Settings
from app.providers.base import (
    AudioFormat,
    ProviderError,
    SpeechStarted,
    SpeechToText,
    SpeechToTextStream,
    STTEvent,
    TranscriptEvent,
)
from app.providers.http import RETRYABLE_STATUSES

NAME = "gnani-prisma"
FRAME_BYTES = 1024  # 512 samples; Prisma wants exactly this size per binary frame
CONNECT_TIMEOUT_S = 5.0
ERROR_CLOSE_GRACE_S = 1.0  # Prisma sends `error` then closes; wait briefly for the close code
_ABNORMAL_CLOSE = 1006


class FrameChunker:
    """Re-slices arbitrary PCM chunks into fixed-size frames, keeping the remainder."""

    def __init__(self, size: int = FRAME_BYTES) -> None:
        self._size = size
        self._rest = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._rest += data
        end = len(self._rest) - len(self._rest) % self._size
        frames = [bytes(self._rest[i : i + self._size]) for i in range(0, end, self._size)]
        del self._rest[:end]
        return frames

    def flush(self) -> bytes | None:
        """The remainder padded with silence to a full frame, or None if there is none."""
        if not self._rest:
            return None
        frame = bytes(self._rest).ljust(self._size, b"\x00")
        self._rest.clear()
        return frame


def close_error(code: int | None, reason: str, server_message: str | None) -> ProviderError | None:
    """Map how the socket ended to an error, or None for a clean end of stream."""
    detail = server_message or reason or f"closed with code {code}"
    if code == 1008:  # bad handshake header or setting: resending the same thing won't help
        return ProviderError(NAME, detail, retryable=False)
    if code in (None, 1000, 1005):
        return ProviderError(NAME, detail, retryable=False) if server_message else None
    # 1001 is the 15-minute session cap; everything else is connection loss. Reconnecting helps.
    return ProviderError(NAME, detail, retryable=True)


class GnaniPrismaSTTStream(SpeechToTextStream):
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._chunker = FrameChunker()
        # One item per VAD segment, so volume is tiny; unbounded keeps close() sentinel-safe.
        self._events: asyncio.Queue[STTEvent | ProviderError | None] = asyncio.Queue()
        self._failure: ProviderError | None = None
        self._closed = False
        self._reader = asyncio.create_task(self._read(), name="prisma-reader")

    async def send_audio(self, chunk: bytes) -> None:
        for frame in self._chunker.push(chunk):
            await self._send(frame)

    async def finish(self) -> None:
        if (tail := self._chunker.flush()) is not None:
            await self._send(tail)
        await self._send(json.dumps({"type": "commit"}))

    async def events(self) -> AsyncIterator[STTEvent]:
        while (item := await self._events.get()) is not None:
            if isinstance(item, ProviderError):
                raise item
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader
        await self._ws.close()
        self._events.put_nowait(None)

    async def _send(self, message: bytes | str) -> None:
        try:
            await self._ws.send(message)
        except ConnectionClosed as exc:
            raise self._failure or self._error_from(exc, None) from exc

    @staticmethod
    def _error_from(exc: ConnectionClosed, server_message: str | None) -> ProviderError:
        code = exc.rcvd.code if exc.rcvd else _ABNORMAL_CLOSE
        reason = exc.rcvd.reason if exc.rcvd else ""
        error = close_error(code, reason, server_message)
        return error or ProviderError(NAME, "stream already closed", retryable=True)

    async def _read(self) -> None:
        server_message: str | None = None
        try:
            async for raw in self._ws:
                if isinstance(raw, str) and (message := json.loads(raw)):
                    if (event := self._to_event(message)) is not None:
                        self._events.put_nowait(event)
                    elif message.get("type") == "error":
                        server_message = str(message.get("message", "unknown error"))
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(self._ws.wait_closed(), ERROR_CLOSE_GRACE_S)
                        break
            self._finish_with(
                close_error(self._ws.close_code, self._ws.close_reason or "", server_message)
            )
        except ConnectionClosed as exc:
            self._finish_with(self._error_from(exc, server_message))
        except json.JSONDecodeError as exc:
            self._finish_with(ProviderError(NAME, f"malformed message: {exc}", retryable=True))

    @staticmethod
    def _to_event(message: dict[str, Any]) -> STTEvent | None:
        match message.get("type"):
            case "speech_start":
                return SpeechStarted()
            case "transcript":
                return TranscriptEvent(
                    text=str(message.get("text", "")),
                    is_final=True,  # Prisma only ever emits finals
                    language=message.get("detected_language_code"),
                )
            case _:  # connected / processing / settings_updated carry nothing we need
                return None

    def _finish_with(self, error: ProviderError | None) -> None:
        self._failure = error
        if error is not None:
            self._events.put_nowait(error)
        self._events.put_nowait(None)


class GnaniPrismaSTT(SpeechToText):
    name = NAME

    def __init__(self, settings: Settings) -> None:
        if settings.gnani_api_key is None:
            raise ValueError("GNANI_API_KEY is required for Gnani Prisma")
        self._settings = settings
        self._api_key = settings.gnani_api_key.get_secret_value()
        self.input_format = AudioFormat(sample_rate=16_000)

    async def open_stream(self, language: str) -> SpeechToTextStream:
        headers = {
            "x-api-key-id": self._api_key,
            "language_code": language,
            "model": self._settings.gnani_stt_model,
            "x-sample-rate": str(self.input_format.sample_rate),
            "format": self._settings.gnani_stt_format,
        }
        try:
            # No client pings: audio flows continuously and Prisma enforces its own idle timeout.
            ws = await connect(
                self._settings.gnani_stt_ws_url,
                additional_headers=headers,
                open_timeout=CONNECT_TIMEOUT_S,
                ping_interval=None,
            )
        except InvalidStatus as exc:  # 401 on a bad key: non-retryable
            status = exc.response.status_code
            retryable = status in RETRYABLE_STATUSES
            raise ProviderError(
                NAME, f"handshake rejected: HTTP {status}", retryable=retryable
            ) from exc
        except (InvalidHandshake, TimeoutError, OSError) as exc:
            raise ProviderError(NAME, f"cannot connect: {exc}", retryable=True) from exc
        return GnaniPrismaSTTStream(ws)
