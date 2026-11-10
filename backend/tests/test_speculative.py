"""Client-driven endpointing and speculative turns: the LLM starts on the transcript, the audio
waits behind a short gate, and speech inside the gate drops the turn without a sound."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from support import (
    LONG_REPLY,
    TWO_SENTENCES,
    AudioFrame,
    ControlledSTT,
    Harness,
    final,
    is_a,
)

import app.conversation.engine as engine_module
import app.conversation.stt_link as stt_link_module
from app.config import Settings
from app.providers.base import SpeechStarted
from app.providers.fakes import FakeLanguageModel, FakeTextToSpeech
from app.voice.state import VoiceState
from app.websocket.protocol import (
    StateChanged,
    TranscriptFinal,
    TurnCancelled,
    TurnCompleted,
    TurnMetrics,
)

S = VoiceState
GRACE_MS = 300


def settings(grace_ms: int = GRACE_MS) -> Settings:
    return Settings(_env_file=None, endpoint_grace_ms=grace_ms)


async def until(condition: Callable[[], bool]) -> None:
    """Poll for up to two seconds."""
    for _ in range(400):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached in time")


class TimedTTS(FakeTextToSpeech):
    """Records when each synthesis was requested."""

    def __init__(self) -> None:
        super().__init__(delay=0.001)
        self.requested_at: list[float] = []

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.requested_at.append(asyncio.get_running_loop().time())
        async for pcm in super().synthesize(text, language=language, voice=voice):
            yield pcm


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_link_module, "REOPEN_BACKOFF_S", 0.01)


async def test_speech_ended_sends_commit_immediately(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, _ = harness.build(stt=stt)
    await engine.start()
    await engine.on_audio(b"\x00" * 640)

    await engine.on_speech_ended()
    await until(lambda: stt.streams[0].finished)  # no further audio needed to flush the utterance

    assert stt.streams[0].audio == [b"\x00" * 640]  # the commit came after the audio, in order


async def test_speculative_turn_cancelled_before_gate_sends_no_audio(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(LONG_REPLY, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings())
    await engine.start()

    stt.streams[0].push(final("what is the weather"))
    await until(lambda: len(llm.calls) == 1)  # the LLM started on the transcript, at once
    await engine.on_speech_started()  # ...but the user kept talking inside the gate
    await asyncio.sleep(GRACE_MS / 1000 + 0.1)

    assert sink.frames == []
    assert sink.states == [S.LISTENING]  # the browser was told nothing about the dropped turn
    assert not sink.of_type(TranscriptFinal)
    assert not sink.of_type(TurnCancelled)
    assert not sink.of_type(TurnMetrics)
    assert engine._turns.current is None  # noqa: SLF001


async def test_prefix_merged_into_next_transcript(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings())
    await engine.start()
    stt.streams[0].push(final("what is the weather"))
    await until(lambda: len(llm.calls) == 1)
    await engine.on_speech_started()

    await engine.on_speech_ended()
    stt.streams[0].push(final("in pune today"))
    await sink.wait_for(is_a(TurnCompleted))

    (transcript,) = sink.of_type(TranscriptFinal)
    assert transcript.text == "what is the weather in pune today"
    assert llm.calls[1][-1].content == "what is the weather in pune today"  # one question, not two
    assert len(llm.calls) == 2
    assert [t.turn_id for t in sink.of_type(TurnCompleted)] == [2]  # ids keep increasing


async def test_two_finals_inside_the_gate_become_one_turn(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings())
    await engine.start()

    stt.streams[0].push(final("tell me"))
    await until(lambda: len(llm.calls) == 1)
    stt.streams[0].push(final("a story"))  # Prisma split the sentence at a pause
    await sink.wait_for(is_a(TurnCompleted))

    assert [t.text for t in sink.of_type(TranscriptFinal)] == ["tell me a story"]
    assert llm.calls[-1][-1].content == "tell me a story"


async def test_gate_opens_after_grace(harness: Harness) -> None:
    stt = ControlledSTT()
    tts = TimedTTS()
    engine, sink = harness.build(stt=stt, tts=tts, settings=settings(150))
    await engine.start()
    loop = asyncio.get_running_loop()

    pushed_at = loop.time()
    stt.streams[0].push(final("hello there"))
    await sink.wait_for(is_a(AudioFrame))
    first_audio_at = loop.time()

    assert first_audio_at - pushed_at >= 0.14  # held for the grace period...
    assert tts.requested_at[0] - pushed_at < 0.1  # ...while synthesis already ran
    assert sink.index(is_a(TranscriptFinal)) < sink.index(is_a(AudioFrame))
    assert sink.states[:4] == [S.LISTENING, S.PROCESSING, S.GENERATING, S.SPEAKING]


async def test_speech_ended_before_the_transcript_skips_the_grace_period(
    harness: Harness,
) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt, settings=settings(5_000))  # an absurd gate
    await engine.start()

    await engine.on_speech_ended()  # the browser already saw 250 ms of silence
    stt.streams[0].push(final("hello there"))

    await sink.wait_for(is_a(AudioFrame))  # would time out if the 5 s gate applied


async def test_speech_after_confirm_contests_and_words_make_it_a_barge_in(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(LONG_REPLY, delay=0.001)
    engine, sink = harness.build(
        stt=stt, llm=llm, tts=FakeTextToSpeech(delay=0.03), settings=settings(20)
    )
    await engine.start()
    stt.streams[0].push(final("tell me a story"))
    await sink.wait_for_state(S.SPEAKING)

    await engine.on_speech_started()
    assert not sink.of_type(TurnCancelled)  # heard speech is contested, not cancelled

    # not merged: the cut-off exchange is history, the next transcript stands alone
    await engine.on_speech_ended()
    stt.streams[0].push(final("sorry go on"))
    await sink.wait_for(lambda i: isinstance(i, TranscriptFinal) and i.turn_id == 2)
    (cancelled,) = sink.of_type(TurnCancelled)
    assert cancelled.reason == "user_spoke"
    assert [t.text for t in sink.of_type(TranscriptFinal)] == ["tell me a story", "sorry go on"]
    assert llm.calls[1][1].content == "tell me a story"


async def test_provider_speech_start_inside_the_gate_also_drops_the_turn_silently(
    harness: Harness,
) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings())
    await engine.start()

    stt.streams[0].push(final("one moment"))
    await until(lambda: len(llm.calls) == 1)
    stt.streams[0].push(SpeechStarted())
    await asyncio.sleep(GRACE_MS / 1000 + 0.1)

    assert sink.frames == [] and sink.states == [S.LISTENING]


async def test_provider_speech_start_for_the_committed_utterance_is_not_a_barge_in(
    harness: Harness,
) -> None:
    """Prisma's `speech_start` can trail our own commit; it belongs to that utterance."""
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings(20))
    await engine.start()
    await engine.on_speech_ended()  # commit sent, transcript pending

    stt.streams[0].push(SpeechStarted())
    stt.streams[0].push(final("hello"))
    await sink.wait_for(is_a(TurnCompleted))

    assert not sink.of_type(TurnCancelled)


async def test_words_heard_while_the_user_talks_again_wait_for_the_rest(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings(20))
    await engine.start()
    await engine.on_speech_started()
    await engine.on_speech_ended()
    await engine.on_speech_started()  # they paused, then went on...

    stt.streams[0].push(final("what about"))  # ...and the first half only now comes back
    await asyncio.sleep(0.1)
    assert llm.calls == []  # no point answering half a question

    await engine.on_speech_ended()
    stt.streams[0].push(final("the weekend"))
    await sink.wait_for(is_a(TurnCompleted))
    assert [t.text for t in sink.of_type(TranscriptFinal)] == ["what about the weekend"]
    assert len(llm.calls) == 1


async def test_held_words_are_answered_if_no_continuation_arrives(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "RESUMED_HOLD_S", 0.05)
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt, settings=settings(20))
    await engine.start()
    await engine.on_speech_started()
    stt.streams[0].push(final("hello there"))  # the "continuation" was only a cough
    await engine.on_speech_ended()

    await sink.wait_for(is_a(TurnCompleted))
    assert [t.text for t in sink.of_type(TranscriptFinal)] == ["hello there"]


async def test_close_drops_a_pending_speculative_turn_without_a_trace(harness: Harness) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(stt=stt, llm=llm, settings=settings(5_000))
    await engine.start()
    stt.streams[0].push(final("hello"))
    await until(lambda: len(llm.calls) == 1)

    await engine.close()

    assert sink.frames == [] and not sink.of_type(TurnMetrics)
    assert not [e for e in sink.of_type(StateChanged) if e.state is S.PROCESSING]
    assert not asyncio.all_tasks() - {asyncio.current_task()}
