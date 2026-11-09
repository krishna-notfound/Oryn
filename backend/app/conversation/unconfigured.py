"""Placeholder conversation used until the real engine is wired into `app.main`."""

from __future__ import annotations

from app.conversation.ports import Conversation, EventSink, UserContext
from app.websocket.protocol import ErrorEvent, SessionStart


class UnconfiguredConversation(Conversation):
    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    async def start(self) -> None:
        await self._sink.send_event(
            ErrorEvent(code="not_configured", message="The conversation engine is not configured.")
        )

    async def on_audio(self, chunk: bytes) -> None: ...

    async def on_audio_end(self) -> None: ...

    async def on_text(self, text: str) -> None: ...

    async def on_interrupt(self) -> None: ...

    async def on_playback_started(self, generation_id: int) -> None: ...

    async def on_playback_finished(self, generation_id: int) -> None: ...

    async def close(self) -> None: ...


def unconfigured_factory(
    user: UserContext, start: SessionStart, sink: EventSink, conversation_id: str
) -> Conversation:
    return UnconfiguredConversation(sink)
