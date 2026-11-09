"""Provider interfaces.

Vendor-specific code (Gnani Prisma, Gnani Timbre, LLM vendors) lives only in
adapters that implement these classes. The rest of the backend depends on
nothing but this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


class ProviderError(Exception):
    """Raised by adapters. `retryable` tells the caller whether a retry can help."""

    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int = 16_000
    channels: int = 1
    encoding: Literal["pcm_s16le"] = "pcm_s16le"


# ---------------------------------------------------------------- speech-to-text


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    text: str
    is_final: bool
    language: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    """Provider-side VAD heard the user start talking (used for server-side barge-in)."""


STTEvent = TranscriptEvent | SpeechStarted


class SpeechToTextStream(ABC):
    """One live recognition stream. Adapters without true streaming may buffer
    audio and transcribe on `finish()` - but must never pretend to stream."""

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None:
        """Push PCM audio in the provider's `input_format`."""

    @abstractmethod
    async def finish(self) -> None:
        """The user stopped speaking; flush and emit a final transcript."""

    @abstractmethod
    def events(self) -> AsyncIterator[STTEvent]:
        """Speech-start signals and partial/final transcripts, in order."""

    @abstractmethod
    async def close(self) -> None:
        """Release the connection. Safe to call twice."""


class SpeechToText(ABC):
    name: str
    input_format: AudioFormat = AudioFormat()

    @abstractmethod
    async def open_stream(self, language: str) -> SpeechToTextStream: ...


# ---------------------------------------------------------------- text-to-speech


class TextToSpeech(ABC):
    name: str
    output_format: AudioFormat = AudioFormat(sample_rate=22_050)

    @abstractmethod
    def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        """Yield PCM chunks in `output_format` as soon as they arrive.
        Cancelling the consuming task must stop the upstream request."""


# ---------------------------------------------------------------- language model


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LLMChunk:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default=())
    finish_reason: str | None = None


class LanguageModel(ABC):
    name: str
    model: str

    @abstractmethod
    def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()
    ) -> AsyncIterator[LLMChunk]:
        """Stream text deltas; tool calls arrive complete in a single chunk."""


# ---------------------------------------------------------------- embeddings


class Embedder(ABC):
    name: str
    dimensions: int

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
