"""Protocol v2: every client message parses, junk is rejected, server events carry the envelope."""

import json
import struct

import pytest
from pydantic import ValidationError

from app.voice.state import VoiceState
from app.websocket.protocol import (
    AUDIO_HEADER,
    AudioStarted,
    ClientTurnMetrics,
    EventEnvelope,
    Heartbeat,
    InputSpeechEnded,
    InputSpeechStarted,
    PlaybackFinished,
    PlaybackStarted,
    SessionEnd,
    SessionReady,
    SessionStart,
    StateChanged,
    TextDelta,
    TurnCancel,
    TurnCancelled,
    UserText,
    encode_event,
    pack_audio_frame,
    parse_client_message,
)

VALID = [
    ('{"v":2,"type":"session.start","token":"t","language":"hi-IN","mode":"voice"}', SessionStart),
    ('{"v":2,"type":"session.start","token":"t","conversation_id":"c1"}', SessionStart),
    ('{"v":2,"type":"input.speech_started","ts":1700000000000}', InputSpeechStarted),
    ('{"v":2,"type":"input.speech_ended","ts":1700000000250}', InputSpeechEnded),
    ('{"v":2,"type":"user.text","text":"hi"}', UserText),
    ('{"v":2,"type":"turn.cancel","generation_id":3}', TurnCancel),
    ('{"v":2,"type":"playback.started","generation_id":3,"client_latency_ms":42.5}', PlaybackStarted),
    ('{"v":2,"type":"playback.started","generation_id":3}', PlaybackStarted),
    ('{"v":2,"type":"playback.finished","generation_id":3}', PlaybackFinished),
    (
        '{"v":2,"type":"turn.metrics","turn_id":1,"generation_id":1,'
        '"perceived_ms":1210.5,"playback_lead_ms":40}',
        ClientTurnMetrics,
    ),
    ('{"v":2,"type":"heartbeat"}', Heartbeat),
    ('{"v":2,"type":"session.end"}', SessionEnd),
]


@pytest.mark.parametrize(("raw", "kind"), VALID)
def test_every_v2_client_message_parses(raw: str, kind: type) -> None:
    assert isinstance(parse_client_message(raw), kind)


@pytest.mark.parametrize(
    "raw",
    [
        '{"v":2,"type":"nope"}',
        '{"v":2,"type":"heartbeat","user_id":"spoofed"}',
        '{"v":1,"type":"heartbeat"}',
        '{"v":2,"type":"audio.end"}',  # v1 names are gone
        '{"v":2,"type":"user.interrupt"}',
        '{"v":2,"type":"turn.cancel"}',  # generation_id is required
        '{"v":2,"type":"user.text","text":""}',
    ],
)
def test_unknown_types_extra_fields_and_missing_fields_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(raw)


def test_audio_frame_header_is_generation_then_sequence() -> None:
    frame = pack_audio_frame(7, 3, b"ab")
    assert frame[:8] == struct.pack(">II", 7, 3)
    assert AUDIO_HEADER.unpack(frame[:8]) == (7, 3) and frame[8:] == b"ab"


def test_server_event_is_wrapped_in_the_envelope() -> None:
    raw = encode_event(
        TextDelta(turn_id=4, generation_id=9, delta="hello"), session_id="s1", seq=12, ts=1700
    )
    assert json.loads(raw) == {
        "v": 2,
        "type": "text.delta",
        "session_id": "s1",
        "turn_id": 4,
        "generation_id": 9,
        "seq": 12,
        "ts": 1700,
        "delta": "hello",
    }
    envelope = EventEnvelope.model_validate_json(raw)
    assert (envelope.type, envelope.turn_id, envelope.generation_id, envelope.seq) == (
        "text.delta",
        4,
        9,
        12,
    )


def test_ids_that_do_not_apply_are_omitted_not_null() -> None:
    body = json.loads(encode_event(StateChanged(state=VoiceState.LISTENING), session_id="s", seq=1, ts=1))
    assert "turn_id" not in body and "generation_id" not in body
    assert body["state"] == "LISTENING"


def test_payload_fields_match_the_spec() -> None:
    ready = json.loads(
        encode_event(
            SessionReady(conversation_id="c", input_sample_rate=16_000, output_sample_rate=24_000),
            session_id="s",
            seq=1,
            ts=1,
        )
    )
    assert ready["protocol"] == 2 and ready["conversation_id"] == "c"
    started = json.loads(
        encode_event(AudioStarted(generation_id=2, sample_rate=24_000), session_id="s", seq=2, ts=1)
    )
    assert started["encoding"] == "pcm_s16le" and started["sample_rate"] == 24_000
    cancelled = json.loads(
        encode_event(
            TurnCancelled(turn_id=1, generation_id=2, reason="barge_in", heard_text="hi"),
            session_id="s",
            seq=3,
            ts=1,
        )
    )
    assert (cancelled["reason"], cancelled["heard_text"]) == ("barge_in", "hi")


def test_non_ascii_text_is_not_escaped() -> None:
    raw = encode_event(TextDelta(delta="नमस्ते"), session_id="s", seq=1, ts=1)
    assert "नमस्ते" in raw
