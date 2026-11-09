import pytest
from pydantic import ValidationError

from app.providers.fakes import FakeLanguageModel, FakeSpeechToText, FakeTextToSpeech
from app.websocket.protocol import (
    AUDIO_HEADER,
    SessionStart,
    UserText,
    pack_audio_frame,
    parse_client_message,
)


def test_parses_client_messages_by_type() -> None:
    assert isinstance(parse_client_message('{"type":"session.start","token":"t"}'), SessionStart)
    assert isinstance(parse_client_message('{"type":"user.text","text":"hi"}'), UserText)


def test_rejects_unknown_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"nope"}')
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"ping","user_id":"spoofed"}')


def test_audio_frame_carries_generation_id() -> None:
    frame = pack_audio_frame(7, b"\x01\x02")
    assert AUDIO_HEADER.unpack(frame[:4]) == (7,) and frame[4:] == b"\x01\x02"


async def test_fake_pipeline_round_trip() -> None:
    stream = await FakeSpeechToText("namaste oryn").open_stream("hi-IN")
    await stream.send_audio(b"\x00" * 640)
    await stream.finish()
    events = [e async for e in stream.events()]
    assert events[-1].is_final and events[-1].text == "namaste oryn"

    reply = "".join([c.text async for c in FakeLanguageModel("hi there").stream([])])
    assert reply.strip() == "hi there"

    audio = [chunk async for chunk in FakeTextToSpeech().synthesize(reply, language="en-IN")]
    assert len(audio) == 2
