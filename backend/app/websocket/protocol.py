"""WebSocket wire contract between the browser and the backend (protocol v2).

Text frames carry JSON. Client messages are `{"v": 2, "type": ..., ...}`. Every server event
is wrapped in an envelope `{"v", "type", "session_id", "turn_id"?, "generation_id"?, "seq", "ts"}`
followed by the event's own fields; `seq` counts events per session and `ts` is server epoch ms.

Binary frames carry audio:
  client -> server : raw PCM s16le mono at 16 kHz, at most MAX_AUDIO_FRAME_BYTES
  server -> client : uint32 BE generation_id + uint32 BE seq (chunk index within the
                     generation) + PCM s16le at AudioStarted.sample_rate
The client drops any event or frame whose generation_id is not the current one, so stale
audio can never play after a barge-in.
Spec: docs/superpowers/specs/2026-11-09-realtime-pipeline-v2-design.md section 7
"""

from __future__ import annotations

import json
import struct
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.voice.state import VoiceState

PROTOCOL_VERSION = 2
MAX_AUDIO_FRAME_BYTES = 32_768
MAX_TEXT_CHARS = 2_000
AUDIO_HEADER = struct.Struct(">II")


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- client -> server


class _ClientMessage(_Message):
    v: Literal[2] = 2


class SessionStart(_ClientMessage):
    type: Literal["session.start"] = "session.start"
    token: str = Field(min_length=1)
    language: str = "en-IN"
    mode: Literal["voice", "text"] = "voice"
    conversation_id: str | None = None


class InputSpeechStarted(_ClientMessage):
    type: Literal["input.speech_started"] = "input.speech_started"
    ts: int | None = None


class InputSpeechEnded(_ClientMessage):
    type: Literal["input.speech_ended"] = "input.speech_ended"
    ts: int | None = None


class UserText(_ClientMessage):
    type: Literal["user.text"] = "user.text"
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class TurnCancel(_ClientMessage):
    type: Literal["turn.cancel"] = "turn.cancel"
    generation_id: int


class PlaybackStarted(_ClientMessage):
    type: Literal["playback.started"] = "playback.started"
    generation_id: int
    client_latency_ms: float | None = None


class PlaybackFinished(_ClientMessage):
    type: Literal["playback.finished"] = "playback.finished"
    generation_id: int


class ClientTurnMetrics(_ClientMessage):
    """What the browser measured; the server logs it next to its own marks."""

    type: Literal["turn.metrics"] = "turn.metrics"
    turn_id: int
    generation_id: int
    perceived_ms: float
    playback_lead_ms: float


class Heartbeat(_ClientMessage):
    type: Literal["heartbeat"] = "heartbeat"


class SessionEnd(_ClientMessage):
    type: Literal["session.end"] = "session.end"


ClientMessage = Annotated[
    SessionStart
    | InputSpeechStarted
    | InputSpeechEnded
    | UserText
    | TurnCancel
    | PlaybackStarted
    | PlaybackFinished
    | ClientTurnMetrics
    | Heartbeat
    | SessionEnd,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    """Raises pydantic.ValidationError on anything malformed."""
    return client_message_adapter.validate_json(raw)


# ---------------------------------------------------------------- server -> client


class _ServerEvent(_Message):
    """Payload of one server event. `turn_id` / `generation_id` move into the envelope."""

    turn_id: int | None = None
    generation_id: int | None = None


class SessionReady(_ServerEvent):
    type: Literal["session.ready"] = "session.ready"
    conversation_id: str
    input_sample_rate: int
    output_sample_rate: int
    protocol: Literal[2] = 2


class StateChanged(_ServerEvent):
    type: Literal["state"] = "state"
    state: VoiceState


class TranscriptFinal(_ServerEvent):
    type: Literal["transcript.final"] = "transcript.final"
    text: str
    language: str | None = None


class LlmStarted(_ServerEvent):
    type: Literal["llm.started"] = "llm.started"


class TextDelta(_ServerEvent):
    type: Literal["text.delta"] = "text.delta"
    delta: str


class TextSegment(_ServerEvent):
    type: Literal["text.segment"] = "text.segment"
    index: int
    text: str


class AudioStarted(_ServerEvent):
    type: Literal["audio.started"] = "audio.started"
    sample_rate: int
    encoding: Literal["pcm_s16le"] = "pcm_s16le"


class AudioSegment(_ServerEvent):
    """Sent just before the first audio chunk of a speakable segment so the client can align
    captions with playback: chunks with seq >= first_seq belong to it until the next segment."""

    type: Literal["audio.segment"] = "audio.segment"
    index: int
    text: str
    first_seq: int


class AudioSegmentDone(_ServerEvent):
    """All audio for segment `index` has been sent; `samples` is its exact length."""

    type: Literal["audio.segment_done"] = "audio.segment_done"
    index: int
    samples: int


class TurnResume(_ServerEvent):
    """The user's overlapping speech was only a backchannel ("haan", "hmm"): keep speaking."""

    type: Literal["turn.resume"] = "turn.resume"


class AudioCompleted(_ServerEvent):
    type: Literal["audio.completed"] = "audio.completed"


class TurnCompleted(_ServerEvent):
    type: Literal["turn.completed"] = "turn.completed"
    text: str


class TurnCancelled(_ServerEvent):
    type: Literal["turn.cancelled"] = "turn.cancelled"
    reason: str
    heard_text: str


class TurnMetrics(_ServerEvent):
    """Server-side marks of one turn. Durations in milliseconds; None when the stage did not run."""

    type: Literal["turn.metrics"] = "turn.metrics"
    stt_ms: float | None = None
    llm_ttft_ms: float | None = None
    segmentation_ms: float | None = None
    tts_slot_wait_ms: float | None = None
    tts_ttfb_ms: float | None = None
    server_first_audio_ms: float | None = None
    speech_end_to_first_audio_ms: float | None = None
    interrupt_to_stop_ms: float | None = None


class PipelineError(_ServerEvent):
    type: Literal["pipeline.error"] = "pipeline.error"
    code: str
    message: str
    retryable: bool = False


class ServerHeartbeat(_ServerEvent):
    type: Literal["heartbeat"] = "heartbeat"


ServerEvent = (
    SessionReady
    | StateChanged
    | TranscriptFinal
    | LlmStarted
    | TextDelta
    | TextSegment
    | AudioStarted
    | AudioSegment
    | AudioSegmentDone
    | TurnResume
    | AudioCompleted
    | TurnCompleted
    | TurnCancelled
    | TurnMetrics
    | PipelineError
    | ServerHeartbeat
)


class EventEnvelope(BaseModel):
    """The envelope fields of any server event, for parsing what `encode_event` produced."""

    model_config = ConfigDict(extra="allow", frozen=True)

    v: Literal[2]
    type: str
    session_id: str
    turn_id: int | None = None
    generation_id: int | None = None
    seq: int
    ts: int


def encode_event(event: ServerEvent, *, session_id: str, seq: int, ts: int) -> str:
    body = event.model_dump(mode="json")
    envelope: dict[str, object] = {
        "v": PROTOCOL_VERSION,
        "type": body.pop("type"),
        "session_id": session_id,
    }
    for key in ("turn_id", "generation_id"):
        if (value := body.pop(key)) is not None:
            envelope[key] = value
    envelope["seq"] = seq
    envelope["ts"] = ts
    return json.dumps(envelope | body, ensure_ascii=False, separators=(",", ":"))


def pack_audio_frame(generation_id: int, seq: int, pcm: bytes) -> bytes:
    return AUDIO_HEADER.pack(generation_id, seq) + pcm
