"""WebSocket wire contract between the browser and the backend.

Text frames carry the JSON messages below. Binary frames carry audio:
  client -> server : raw PCM s16le mono at SessionReady.input_sample_rate
  server -> client : 4-byte big-endian uint32 generation_id + PCM s16le
                     at AssistantAudioStart.sample_rate
The client drops any audio frame whose generation_id is not the current one,
so stale audio can never play after a barge-in.
Spec: docs/context/websocket-contract.md
"""

from __future__ import annotations

import struct
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.voice.state import VoiceState

MAX_AUDIO_FRAME_BYTES = 32_768
MAX_TEXT_CHARS = 2_000
AUDIO_HEADER = struct.Struct(">I")


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- client -> server


class SessionStart(_Message):
    type: Literal["session.start"] = "session.start"
    token: str = Field(min_length=1)
    language: str = "en-IN"
    mode: Literal["voice", "text"] = "voice"
    conversation_id: str | None = None


class UserText(_Message):
    type: Literal["user.text"] = "user.text"
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class UserInterrupt(_Message):
    type: Literal["user.interrupt"] = "user.interrupt"


class AudioEnd(_Message):
    type: Literal["audio.end"] = "audio.end"


class PlaybackStarted(_Message):
    type: Literal["playback.started"] = "playback.started"
    generation_id: int


class PlaybackFinished(_Message):
    type: Literal["playback.finished"] = "playback.finished"
    generation_id: int


class SessionEnd(_Message):
    type: Literal["session.end"] = "session.end"


class Ping(_Message):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    SessionStart
    | UserText
    | UserInterrupt
    | AudioEnd
    | PlaybackStarted
    | PlaybackFinished
    | SessionEnd
    | Ping,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    """Raises pydantic.ValidationError on anything malformed."""
    return client_message_adapter.validate_json(raw)


# ---------------------------------------------------------------- server -> client


class SessionReady(_Message):
    type: Literal["session.ready"] = "session.ready"
    session_id: str
    conversation_id: str
    input_sample_rate: int
    output_sample_rate: int


class StateChanged(_Message):
    type: Literal["state"] = "state"
    state: VoiceState


class TranscriptPartial(_Message):
    type: Literal["transcript.partial"] = "transcript.partial"
    text: str


class TranscriptFinal(_Message):
    type: Literal["transcript.final"] = "transcript.final"
    turn_id: int
    text: str
    language: str | None = None


class AssistantText(_Message):
    type: Literal["assistant.text"] = "assistant.text"
    generation_id: int
    delta: str


class AssistantAudioStart(_Message):
    type: Literal["assistant.audio.start"] = "assistant.audio.start"
    generation_id: int
    sample_rate: int
    encoding: Literal["pcm_s16le"] = "pcm_s16le"


class AssistantDone(_Message):
    type: Literal["assistant.done"] = "assistant.done"
    generation_id: int
    text: str


class Interrupted(_Message):
    type: Literal["interrupted"] = "interrupted"
    generation_id: int


class TurnMetrics(_Message):
    """All durations in milliseconds; None when the stage did not run."""

    type: Literal["metrics"] = "metrics"
    turn_id: int
    generation_id: int
    stt_ms: float | None = None
    llm_ttft_ms: float | None = None
    tts_ttfb_ms: float | None = None
    speech_end_to_first_audio_ms: float | None = None
    interrupt_to_stop_ms: float | None = None


class ErrorEvent(_Message):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


class Pong(_Message):
    type: Literal["pong"] = "pong"


ServerEvent = (
    SessionReady
    | StateChanged
    | TranscriptPartial
    | TranscriptFinal
    | AssistantText
    | AssistantAudioStart
    | AssistantDone
    | Interrupted
    | TurnMetrics
    | ErrorEvent
    | Pong
)


def pack_audio_frame(generation_id: int, pcm: bytes) -> bytes:
    return AUDIO_HEADER.pack(generation_id) + pcm
