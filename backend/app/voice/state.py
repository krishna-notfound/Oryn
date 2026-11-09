"""Voice session states and the machine that guards transitions between them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum


class VoiceState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    GENERATING = "GENERATING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


S = VoiceState
_ALWAYS_ALLOWED = frozenset({S.RECONNECTING, S.ERROR, S.IDLE})  # connection lost / fatal / closed
_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    S.IDLE: frozenset({S.LISTENING}),
    S.LISTENING: frozenset({S.PROCESSING}),
    S.PROCESSING: frozenset({S.GENERATING, S.INTERRUPTED}),
    S.GENERATING: frozenset({S.SPEAKING, S.LISTENING, S.INTERRUPTED}),  # LISTENING: text-only reply
    S.SPEAKING: frozenset({S.LISTENING, S.INTERRUPTED}),
    S.INTERRUPTED: frozenset({S.LISTENING}),
    S.RECONNECTING: frozenset({S.LISTENING}),
    S.ERROR: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, source: VoiceState, target: VoiceState) -> None:
        super().__init__(f"invalid voice state transition {source} -> {target}")


class VoiceStateMachine:
    """Single source of truth for the session state. The new state is stored before
    `on_change` runs, so a cancelled callback never leaves the machine stale."""

    def __init__(self, on_change: Callable[[VoiceState], Awaitable[None]] | None = None) -> None:
        self._state = VoiceState.IDLE
        self._on_change = on_change

    @property
    def state(self) -> VoiceState:
        return self._state

    async def transition(self, to: VoiceState) -> None:
        if to not in _TRANSITIONS[self._state] and to not in _ALWAYS_ALLOWED:
            raise InvalidTransition(self._state, to)
        self._state = to
        if self._on_change is not None:
            await self._on_change(to)
