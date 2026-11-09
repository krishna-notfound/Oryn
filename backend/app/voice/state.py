"""Voice session states. The transition table and state machine are added in F09."""

from __future__ import annotations

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
