"""Shared test doubles: a recording sink, scripted STT/LLM/TTS, and an engine harness."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.conversation.engine import VoiceConversation
from app.conversation.ports import EventSink, Memory, MemoryRetriever, ToolRunner, UserContext
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    LLMChunk,
    ProviderError,
    SpeechToText,
    SpeechToTextStream,
    STTEvent,
    TextToSpeech,
    ToolCall,
    ToolSpec,
    TranscriptEvent,
)
from app.providers.fakes import FakeLanguageModel, FakeSpeechToText, FakeTextToSpeech
from app.voice.state import VoiceState
from app.websocket.protocol import ServerEvent, StateChanged

TWO_SENTENCES = "Alpha beta gamma. Delta epsilon zeta."
LONG_REPLY = "One two three. Four five six. Seven eight nine. Ten eleven twelve."


# ---------------------------------------------------------------- test doubles


@dataclass(frozen=True)
class AudioFrame:
    generation_id: int
    seq: int
    pcm: bytes


class RecordingSink(EventSink):
    """Records events and audio frames in the exact order they were sent."""

    def __init__(self) -> None:
        self.timeline: list[ServerEvent | AudioFrame] = []
        self._changed = asyncio.Event()

    async def send_event(self, event: ServerEvent) -> None:
        self._record(event)

    async def send_audio(self, generation_id: int, seq: int, pcm: bytes) -> None:
        self._record(AudioFrame(generation_id, seq, pcm))

    def _record(self, item: ServerEvent | AudioFrame) -> None:
        self.timeline.append(item)
        self._changed.set()

    async def _until(self, condition: Callable[[], bool]) -> None:
        async with asyncio.timeout(2):
            while not condition():
                self._changed.clear()
                await self._changed.wait()

    @property
    def events(self) -> list[ServerEvent]:
        return [i for i in self.timeline if not isinstance(i, AudioFrame)]

    @property
    def frames(self) -> list[AudioFrame]:
        return [i for i in self.timeline if isinstance(i, AudioFrame)]

    def of_type[T](self, kind: type[T]) -> list[T]:
        return [e for e in self.events if isinstance(e, kind)]

    @property
    def states(self) -> list[VoiceState]:
        return [e.state for e in self.of_type(StateChanged)]

    def index(self, predicate: Callable[[ServerEvent | AudioFrame], bool]) -> int:
        return next(i for i, item in enumerate(self.timeline) if predicate(item))

    async def wait_for(self, predicate: Callable[[ServerEvent | AudioFrame], bool]) -> None:
        await self._until(lambda: any(predicate(item) for item in self.timeline))

    async def wait_for_state(self, state: VoiceState, count: int = 1) -> None:
        await self._until(lambda: self.states.count(state) >= count)


def is_a(kind: type) -> Callable[[Any], bool]:
    return lambda item: isinstance(item, kind)


class ControlledStream(SpeechToTextStream):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[STTEvent | BaseException | None] = asyncio.Queue()
        self.audio: list[bytes] = []
        self.finished = False
        self.closed = False

    def push(self, item: STTEvent | BaseException | None) -> None:
        self._queue.put_nowait(item)

    async def send_audio(self, chunk: bytes) -> None:
        self.audio.append(chunk)

    async def finish(self) -> None:
        self.finished = True

    async def events(self) -> AsyncIterator[STTEvent]:
        while (item := await self._queue.get()) is not None:
            if isinstance(item, BaseException):
                raise item
            yield item

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


class ControlledSTT(SpeechToText):
    name = "controlled-stt"

    def __init__(self) -> None:
        self.streams: list[ControlledStream] = []
        self.open_errors: list[ProviderError] = []

    async def open_stream(self, language: str) -> SpeechToTextStream:
        if self.open_errors:
            raise self.open_errors.pop(0)
        stream = ControlledStream()
        self.streams.append(stream)
        return stream


def final(text: str) -> TranscriptEvent:
    return TranscriptEvent(text, is_final=True, language="en-IN")


class ScriptedLLM(LanguageModel):
    """Plays one scripted list of chunks per stream() call; records what it was sent."""

    name = "scripted-llm"
    model = "scripted"

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls: list[list[ChatMessage]] = []
        self.max_tokens: list[int | None] = []
        self.tool_specs: list[Sequence[ToolSpec]] = []

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        self.calls.append(list(messages))
        self.tool_specs.append(tools)
        self.max_tokens.append(max_tokens)
        for chunk in self._rounds[len(self.calls) - 1]:
            await asyncio.sleep(0)
            yield chunk


class FailingLLM(LanguageModel):
    name = "failing-llm"
    model = "failing"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        raise self._error
        yield LLMChunk()  # pragma: no cover - makes this an async generator


class FailingTTS(FakeTextToSpeech):
    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        raise ProviderError("timbre", "socket closed", retryable=True)
        yield b""  # pragma: no cover


class TaggedTTS(TextToSpeech):
    """Audio is the sentence text itself, so order is visible; the first sentence is
    slow on purpose so a naive implementation would emit the others first."""

    name = "tagged-tts"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.started: list[str] = []

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.started.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.03 if text.startswith("One") else 0.002)
            for word in text.split():
                await asyncio.sleep(0.002)
                yield word.encode()
        finally:
            self.active -= 1


class ScriptedTools(ToolRunner):
    def __init__(self) -> None:
        self.runs: list[ToolCall] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec("weather", "Look up weather", {"type": "object"})]

    async def run(self, call: ToolCall, user: UserContext) -> str:
        self.runs.append(call)
        return "SECRET-TOOL-RESULT 31C"


class FixedMemory(MemoryRetriever):
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    async def relevant(self, user_id: str, query: str, limit: int = 5) -> list[Memory]:
        await asyncio.sleep(self.delay)
        return [Memory(id="m1", content="Has a dog named Bruno", category="fact")]


# ---------------------------------------------------------------- fixtures


class Harness:
    def __init__(self) -> None:
        self.engines: list[VoiceConversation] = []

    def build(
        self,
        *,
        mode: str = "voice",
        llm: LanguageModel | None = None,
        tts: TextToSpeech | None = None,
        stt: SpeechToText | None = None,
        user: UserContext | None = None,
        settings: Settings | None = None,
        **hooks: Any,
    ) -> tuple[VoiceConversation, RecordingSink]:
        sink = RecordingSink()
        engine = VoiceConversation(
            user=user or UserContext(user_id="u1"),
            mode=mode,  # type: ignore[arg-type]
            stt=stt or FakeSpeechToText(TWO_SENTENCES),
            llm=llm or FakeLanguageModel(TWO_SENTENCES),
            tts=tts or FakeTextToSpeech(delay=0.002),
            sink=sink,
            settings=settings or Settings(_env_file=None, endpoint_grace_ms=20),
            **hooks,
        )
        self.engines.append(engine)
        return engine, sink
