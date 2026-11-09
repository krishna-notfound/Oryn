"""Deterministic in-memory providers for tests, CI and offline development
(PROVIDER_MODE=fake). They follow the same timing shape as real providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.providers.base import (
    AudioFormat,
    ChatMessage,
    Embedder,
    LanguageModel,
    LLMChunk,
    SpeechToText,
    SpeechStarted,
    SpeechToTextStream,
    STTEvent,
    TextToSpeech,
    ToolSpec,
    TranscriptEvent,
)


class FakeSpeechToTextStream(SpeechToTextStream):
    def __init__(self, transcript: str, delay: float) -> None:
        self._transcript = transcript
        self._delay = delay
        self._queue: asyncio.Queue[STTEvent | None] = asyncio.Queue()
        self._bytes_seen = 0
        self._started = False

    async def send_audio(self, chunk: bytes) -> None:
        self._bytes_seen += len(chunk)
        if not self._started:
            self._started = True
            await self._queue.put(SpeechStarted())

    async def finish(self) -> None:
        await asyncio.sleep(self._delay)
        await self._queue.put(TranscriptEvent(self._transcript, is_final=True, language="en-IN"))
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[STTEvent]:
        while (event := await self._queue.get()) is not None:
            yield event

    async def close(self) -> None:
        await self._queue.put(None)


class FakeSpeechToText(SpeechToText):
    name = "fake-stt"

    def __init__(self, transcript: str = "hello oryn how are you", delay: float = 0.01) -> None:
        self.transcript = transcript
        self.delay = delay

    async def open_stream(self, language: str) -> SpeechToTextStream:
        return FakeSpeechToTextStream(self.transcript, self.delay)


class FakeTextToSpeech(TextToSpeech):
    """Emits 20 ms of silence per word so playback timing is realistic."""

    name = "fake-tts"
    output_format = AudioFormat(sample_rate=16_000)

    def __init__(self, delay: float = 0.005) -> None:
        self.delay = delay

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        frame = bytes(2 * self.output_format.sample_rate // 50)
        for _ in text.split():
            await asyncio.sleep(self.delay)
            yield frame


class FakeLanguageModel(LanguageModel):
    name = "fake-llm"
    model = "fake"

    def __init__(
        self, reply: str = "I'm doing well. What's on your mind today?", delay: float = 0.005
    ) -> None:
        self.reply = reply
        self.delay = delay
        self.calls: list[list[ChatMessage]] = []

    async def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()
    ) -> AsyncIterator[LLMChunk]:
        self.calls.append(list(messages))
        for word in self.reply.split(" "):
            await asyncio.sleep(self.delay)
            yield LLMChunk(text=word + " ")
        yield LLMChunk(finish_reason="stop")


class FakeEmbedder(Embedder):
    """Bag-of-characters embedding: similar strings get similar vectors."""

    name = "fake-embedder"
    dimensions = 32

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for ch in text.lower():
                vec[ord(ch) % self.dimensions] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors
