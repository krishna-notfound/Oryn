import pytest
from pydantic import ValidationError

from app.providers.fakes import FakeLanguageModel, FakeSpeechToText, FakeTextToSpeech
from app.websocket.protocol import SessionStart, UserText, parse_client_message


def test_parses_client_messages_by_type() -> None:
    assert isinstance(parse_client_message('{"v":2,"type":"session.start","token":"t"}'), SessionStart)
    assert isinstance(parse_client_message('{"v":2,"type":"user.text","text":"hi"}'), UserText)


def test_rejects_unknown_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"type":"nope"}')
    with pytest.raises(ValidationError):
        parse_client_message('{"v":2,"type":"heartbeat","user_id":"spoofed"}')


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
