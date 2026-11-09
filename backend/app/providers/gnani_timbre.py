"""Gnani Timbre text-to-speech over Server-Sent Events.

Transport choice: SSE over one long-lived pooled `httpx.AsyncClient`, not the TTS WebSocket.
Keep-alive removes the per-sentence TLS handshake, and every synthesis is an independent
request, so cancelling one (barge-in) just closes its response. The WebSocket has no cancel
message, so aborting mid-synthesis would mean dropping the socket (a fresh handshake) or
draining stale audio, and the docs recommend one request at a time per socket anyway. They
also contradict themselves on whether config goes in upgrade headers or the JSON body.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any

import httpx

from app.config import Settings
from app.providers.base import AudioFormat, ProviderError, TextToSpeech
from app.providers.http import error_from_response, iter_sse

log = logging.getLogger(__name__)

NAME = "gnani-timbre"
RETRY_BACKOFF_S = (0.5, 1.0, 2.0)


def strip_wav_header(data: bytes) -> bytes:
    """Drop a RIFF/WAV header if present; the audio starts after the `data` chunk header."""
    if not data.startswith(b"RIFF"):
        return data
    marker = data.find(b"data", 12)
    return data[marker + 8 :] if marker != -1 else b""


class GnaniTimbreTTS(TextToSpeech):
    name = NAME

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        backoff: Sequence[float] = RETRY_BACKOFF_S,
    ) -> None:
        if settings.gnani_api_key is None:
            raise ValueError("GNANI_API_KEY is required for Gnani Timbre")
        self._settings = settings
        self._client = client
        self._backoff = tuple(backoff)
        self._url = f"{settings.gnani_base_url.rstrip('/')}/api/v1/tts/sse"
        self._headers = {"X-API-Key-ID": settings.gnani_api_key.get_secret_value()}
        self.output_format = AudioFormat(sample_rate=settings.gnani_tts_sample_rate)

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        body = self._request_body(text, language, voice)
        for delay in (*self._backoff, None):
            started = False
            try:
                # aclosing: a cancelled consumer must close the HTTP response immediately.
                async with contextlib.aclosing(self._stream_once(body)) as chunks:
                    async for pcm in chunks:
                        started = True
                        yield pcm
                return
            except ProviderError as exc:
                if started or not exc.retryable or delay is None:
                    raise
                log.warning("tts retry", extra={"delay_s": delay, "error": str(exc)})
            await asyncio.sleep(delay)

    def _request_body(self, text: str, language: str, voice: str | None) -> dict[str, Any]:
        s = self._settings
        return {
            "text": text,
            "model": s.gnani_tts_model,  # always explicit: the API rejects the SDK default
            "voice": voice or s.gnani_tts_voice,
            "language": language,
            "audio_config": {
                "sample_rate": self.output_format.sample_rate,
                "num_channels": self.output_format.channels,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "raw",
            },
        }

    async def _stream_once(self, body: dict[str, Any]) -> AsyncGenerator[bytes]:
        spare = b""  # a chunk boundary must not split a 16-bit sample
        try:
            async with self._client.stream(
                "POST", self._url, json=body, headers=self._headers
            ) as response:
                if response.status_code != 200:
                    raise await error_from_response(NAME, response)
                async for event, data in iter_sse(response):
                    payload: dict[str, Any] = json.loads(data)
                    if event == "error" or payload.get("type") == "error":
                        raise ProviderError(NAME, str(payload.get("message", "stream error")))
                    if not payload.get("audio"):  # `start` and `complete` events carry no audio
                        continue
                    pcm = spare + strip_wav_header(base64.b64decode(payload["audio"]))
                    pcm, spare = pcm[: len(pcm) - len(pcm) % 2], pcm[len(pcm) - len(pcm) % 2 :]
                    if pcm:
                        yield pcm
        except httpx.TransportError as exc:
            raise ProviderError(NAME, f"transport error: {exc!r}", retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(NAME, f"malformed SSE payload: {exc}") from exc
