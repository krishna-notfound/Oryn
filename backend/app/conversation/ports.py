"""Seams between the WebSocket layer, the conversation engine, memory and tools.

The WebSocket session (F05) talks to a `Conversation`; the engine (F07/F09)
talks back through an `EventSink`. Memory (F12) and tools (F13) plug in
through `MemoryRetriever` and `ToolRunner`. Nobody imports anyone's internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from app.providers.base import ToolCall, ToolSpec
from app.websocket.protocol import ServerEvent


@dataclass(frozen=True, slots=True)
class UserContext:
    """Verified identity plus the preferences that shape replies."""

    user_id: str
    preferred_name: str | None = None
    language: str = "en-IN"
    extra_languages: tuple[str, ...] = ()
    style: str = "friendly"  # friendly | playful | calm | thoughtful | direct
    response_length: Literal["short", "detailed"] = "short"
    listen_first: bool = True
    memory_enabled: bool = False
    save_history: bool = True
    timezone: str = "Asia/Kolkata"
    interests: tuple[str, ...] = field(default=())


class EventSink(ABC):
    """Where the engine sends things the browser should see or hear."""

    @abstractmethod
    async def send_event(self, event: ServerEvent) -> None: ...

    @abstractmethod
    async def send_audio(self, generation_id: int, pcm: bytes) -> None: ...


class Conversation(ABC):
    """One live conversation. Every method returns quickly; long work runs in
    background tasks owned by the implementation and is cancelled by close()."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def on_audio(self, chunk: bytes) -> None: ...

    @abstractmethod
    async def on_audio_end(self) -> None: ...

    @abstractmethod
    async def on_text(self, text: str) -> None: ...

    @abstractmethod
    async def on_interrupt(self) -> None: ...

    @abstractmethod
    async def on_playback_started(self, generation_id: int) -> None: ...

    @abstractmethod
    async def on_playback_finished(self, generation_id: int) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Memory:
    id: str
    content: str
    category: str
    confidence: float = 1.0


class MemoryRetriever(ABC):
    @abstractmethod
    async def relevant(self, user_id: str, query: str, limit: int = 5) -> list[Memory]: ...


class ToolRunner(ABC):
    @abstractmethod
    def specs(self) -> list[ToolSpec]: ...

    @abstractmethod
    async def run(self, call: ToolCall, user: UserContext) -> str:
        """Return a short result string for the model. Never raises for tool
        failures - returns an error description instead."""
