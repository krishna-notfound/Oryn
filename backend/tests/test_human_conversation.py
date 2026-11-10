"""The human-conversation features, end to end through the engine with fake providers:
contested turns (backchannel vs real interruption), the one-turn interruption note, reply
length, the rolling summary, per-turn language and voice, and caption events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from support import (
    LONG_REPLY,
    TWO_SENTENCES,
    AudioFrame,
    ControlledSTT,
    Harness,
    RecordingSink,
    ScriptedLLM,
    ScriptedTools,
    TaggedTTS,
    final,
    is_a,
)

import app.conversation.engine as engine_module
import app.conversation.pipeline as pipeline_module
from app.config import Settings
from app.conversation.ports import UserContext
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    LLMChunk,
    SpeechStarted,
    ToolCall,
    ToolSpec,
    TranscriptEvent,
)
from app.providers.fakes import FakeLanguageModel, FakeTextToSpeech
from app.voice.catalog import resolve_voice
from app.voice.state import VoiceState
from app.websocket.protocol import (
    AudioSegment,
    AudioSegmentDone,
    TranscriptFinal,
    TurnCancelled,
    TurnCompleted,
    TurnResume,
)

S = VoiceState
SLOW_TTS = 0.03


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A hand-set clock for the engine and pipeline, to test what was heard."""
    now = [100.0]
    monkeypatch.setattr(engine_module, "perf_counter", lambda: now[0])
    monkeypatch.setattr(pipeline_module, "perf_counter", lambda: now[0])
    return now


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, endpoint_grace_ms=20, **overrides)


async def start_speaking(
    harness: Harness, *, reply: str = LONG_REPLY, **build: Any
) -> tuple[Any, ControlledSTT, RecordingSink, FakeLanguageModel]:
    """An engine whose first reply is audible and still going."""
    stt = ControlledSTT()
    llm = build.pop("llm", None) or FakeLanguageModel(reply, delay=0.001)
    build.setdefault("tts", FakeTextToSpeech(delay=SLOW_TTS))
    build.setdefault("settings", settings())
    engine, sink = harness.build(stt=stt, llm=llm, **build)
    await engine.start()
    stt.streams[0].push(final("tell me about monsoon"))
    await sink.wait_for_state(S.SPEAKING)
    return engine, stt, sink, llm


# ---------------------------------------------------------------- contested turns


async def test_speech_over_the_reply_ducks_nothing_and_cancels_nothing_until_words_arrive(
    harness: Harness,
) -> None:
    engine, _, sink, _ = await start_speaking(harness)
    await engine.on_speech_started()
    frames = len(sink.frames)

    await sink.wait_for(lambda i: isinstance(i, AudioFrame) and i.seq > frames)
    assert not sink.of_type(TurnCancelled) and not sink.of_type(TurnResume)


@pytest.mark.parametrize("nod", ["haan", "Hmm.", "ok ok", "हाँ", "अच्छा ठीक", "haha", "  "])
async def test_a_backchannel_resumes_the_reply_and_is_not_a_turn(
    harness: Harness, nod: str
) -> None:
    engine, stt, sink, llm = await start_speaking(harness)
    await engine.on_speech_started()
    await engine.on_speech_ended()
    stt.streams[0].push(TranscriptEvent(nod, is_final=True, language="hi-IN"))
    await sink.wait_for(is_a(TurnResume))
    await sink.wait_for(is_a(TurnCompleted))

    (resume,) = sink.of_type(TurnResume)
    assert (resume.turn_id, resume.generation_id) == (1, 1)
    assert not sink.of_type(TurnCancelled)
    assert len(llm.calls) == 1  # the nod did not start a turn
    assert len(sink.of_type(TurnCompleted)) == 1
    spoken = sum(len(f.pcm) for f in sink.frames)
    assert spoken == len(LONG_REPLY.split()) * 640  # every word of the reply still played


async def test_a_resumed_reply_is_remembered_in_full(harness: Harness) -> None:
    engine, stt, sink, llm = await start_speaking(harness)
    await engine.on_speech_started()
    stt.streams[0].push(final("haan"))
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_finished(1)
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("and then?")
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)

    history = [(m.role, m.content) for m in llm.calls[1][1:]]
    assert history == [
        ("user", "tell me about monsoon"),
        ("assistant", LONG_REPLY),
        ("user", "and then?"),
    ]
    assert "cut off" not in llm.calls[1][0].content


async def test_real_words_cancel_the_reply_and_start_the_next_turn(harness: Harness) -> None:
    engine, stt, sink, llm = await start_speaking(harness)
    await engine.on_speech_started()
    await engine.on_speech_ended()
    stt.streams[0].push(final("actually tell me about food"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)

    (cancelled,) = sink.of_type(TurnCancelled)
    assert cancelled.reason == "user_spoke" and cancelled.generation_id == 1
    assert not sink.of_type(TurnResume)
    assert sink.index(is_a(TurnCancelled)) < sink.index(
        lambda i: isinstance(i, AudioFrame) and i.generation_id == 2
    )
    assert not [
        f
        for f in sink.timeline[sink.index(is_a(TurnCancelled)) :]
        if isinstance(f, AudioFrame) and f.generation_id == 1
    ]
    assert llm.calls[1][-1].content == "actually tell me about food"


async def test_the_next_turn_after_an_interruption_gets_a_one_turn_note(
    harness: Harness, clock: list[float]
) -> None:
    engine, stt, sink, llm = await start_speaking(harness, llm=FakeLanguageModel(TWO_SENTENCES))
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_started(1)
    clock[0] += 0.08  # the first sentence played, the second did not
    await engine.on_speech_started()
    stt.streams[0].push(final("wait one thing"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)

    note = llm.calls[1][0].content
    assert 'You were cut off while saying: "Alpha beta gamma."' in note
    assert "Do not mention being interrupted" in note
    assert llm.calls[1][2].content == "Alpha beta gamma."  # history holds the heard text only

    await engine.on_playback_finished(2)
    await sink.wait_for_state(S.LISTENING, 3)
    await engine.on_text("thanks")
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 3)
    assert "cut off" not in llm.calls[2][0].content  # one turn only
    assert all("[interrupted" not in m.content for call in llm.calls for m in call)


async def test_a_cancel_from_the_client_is_immediate_even_while_contested(
    harness: Harness,
) -> None:
    engine, _, sink, _ = await start_speaking(harness)
    await engine.on_speech_started()
    await engine.on_cancel(1)

    (cancelled,) = sink.of_type(TurnCancelled)
    assert cancelled.reason == "client_cancel"
    assert not engine._arbiter.contested
    assert sink.states[-2:] == [S.INTERRUPTED, S.LISTENING]


async def test_without_a_transcript_the_contest_lapses_and_the_client_is_told(
    harness: Harness,
) -> None:
    engine, _, sink, _ = await start_speaking(harness, settings=settings(contest_timeout_s=0.05))
    await engine.on_speech_started()
    await engine.on_speech_ended()  # they stopped, and no transcript ever comes
    await sink.wait_for(is_a(TurnResume))
    await sink.wait_for(is_a(TurnCompleted))

    assert not sink.of_type(TurnCancelled)
    assert not engine._arbiter.contested


async def test_a_long_interruption_is_not_timed_out_while_the_user_is_still_talking(
    harness: Harness,
) -> None:
    engine, stt, sink, _ = await start_speaking(harness, settings=settings(contest_timeout_s=0.05))
    await engine.on_speech_started()
    await asyncio.sleep(0.2)  # well past the timeout, but the browser says they are still talking
    assert not sink.of_type(TurnResume) and engine._arbiter.contested

    await engine.on_speech_ended()
    stt.streams[0].push(final("hold on, a different question"))
    await sink.wait_for(is_a(TurnCancelled))
    assert sink.of_type(TurnCancelled)[0].reason == "user_spoke"


async def test_the_contest_ends_with_the_reply(harness: Harness) -> None:
    """A late 'haan' after the reply is over is just the user answering: a normal turn."""
    engine, stt, sink, llm = await start_speaking(harness, llm=FakeLanguageModel(TWO_SENTENCES))
    await engine.on_speech_started()
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_finished(1)
    await sink.wait_for_state(S.LISTENING, 2)
    assert not engine._arbiter.contested

    stt.streams[0].push(final("haan"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)
    assert llm.calls[1][-1].content == "haan" and not sink.of_type(TurnResume)


async def test_speech_inside_the_speculative_gate_still_drops_the_turn_silently(
    harness: Harness,
) -> None:
    stt = ControlledSTT()
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(
        stt=stt, llm=llm, settings=Settings(_env_file=None, endpoint_grace_ms=300)
    )
    await engine.start()
    stt.streams[0].push(final("one moment"))
    await asyncio.sleep(0.05)  # the speculative turn is under way, its audio gated
    assert llm.calls
    await engine.on_speech_started()
    await asyncio.sleep(0.4)

    assert sink.frames == [] and not sink.of_type(TurnCancelled) and not sink.of_type(TurnResume)


async def test_stt_speech_start_is_not_a_hard_cancel_but_the_client_message_counts_too(
    harness: Harness,
) -> None:
    engine, stt, sink, _ = await start_speaking(harness)
    stt.streams[0].push(SpeechStarted())
    await asyncio.sleep(0.02)
    assert engine._arbiter.contested and not sink.of_type(TurnCancelled)


# ---------------------------------------------------------------- reply length


async def test_story_requests_get_the_long_token_budget(harness: Harness) -> None:
    llm = FakeLanguageModel("Once upon a time.")
    engine, sink = harness.build(mode="text", llm=llm)
    await engine.start()
    await engine.on_text("tell me a story")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("what is the time")
    await sink.wait_for_state(S.LISTENING, 3)

    assert llm.max_tokens == [1200, 400]


async def test_the_budget_applies_to_every_round_of_the_tool_loop(harness: Harness) -> None:
    call = LLMChunk(tool_calls=(ToolCall("c1", "weather", {}),))
    llm = ScriptedLLM([[call], [LLMChunk(text="Warm. ")]])
    engine, sink = harness.build(mode="text", llm=llm, tools=ScriptedTools())
    await engine.start()
    await engine.on_text("explain the weather")
    await sink.wait_for(is_a(TurnCompleted))
    assert llm.max_tokens == [1200, 1200]


# ---------------------------------------------------------------- rolling summary


class SummaryAwareLLM(LanguageModel):
    """Answers chat normally; a summarisation request waits for `release` first."""

    name = "summary-aware"
    model = "summary-aware"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.chat_calls: list[list[ChatMessage]] = []
        self.summary_started = asyncio.Event()

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        if messages[0].content.startswith("You keep a running summary"):
            self.summary_started.set()
            await self.release.wait()
            yield LLMChunk(text="They discussed monsoon plans.")
            return
        self.chat_calls.append(list(messages))
        yield LLMChunk(text="Sure thing.")


async def test_the_summary_is_built_in_the_background_and_reaches_a_later_prompt(
    harness: Harness,
) -> None:
    llm = SummaryAwareLLM()
    engine, sink = harness.build(mode="text", llm=llm, settings=settings(context_max_turns=2))
    await engine.start()
    for n in range(1, 4):  # the third exchange pushes the first out of the two kept verbatim
        await engine.on_text(f"question {n}")
        await sink.wait_for_state(S.LISTENING, n + 1)
    await engine.on_text("question 4")  # the summary call is still blocked: replying must not wait
    await sink.wait_for_state(S.LISTENING, 5)
    assert llm.summary_started.is_set()
    assert "Conversation so far" not in llm.chat_calls[-1][0].content

    llm.release.set()
    await engine._summary.settle()
    await engine.on_text("question 5")
    await sink.wait_for_state(S.LISTENING, 6)
    assert "Conversation so far: They discussed monsoon plans." in llm.chat_calls[-1][0].content


async def test_a_failing_summary_never_breaks_the_conversation(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    class Flaky(SummaryAwareLLM):
        async def stream(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[ToolSpec] = (),
            *,
            max_tokens: int | None = None,
        ) -> AsyncIterator[LLMChunk]:
            if messages[0].content.startswith("You keep a running summary"):
                raise RuntimeError("summary llm down")
            async for chunk in super().stream(messages, tools, max_tokens=max_tokens):
                yield chunk

    engine, sink = harness.build(mode="text", llm=Flaky(), settings=settings(context_max_turns=1))
    await engine.start()
    for n in range(1, 4):
        await engine.on_text(f"question {n}")
        await sink.wait_for_state(S.LISTENING, n + 1)
    await engine._summary.settle()
    assert "summary update failed" in caplog.text
    assert len(sink.of_type(TurnCompleted)) == 3


# ---------------------------------------------------------------- language and voice


class RecordingTTS(FakeTextToSpeech):
    def __init__(self) -> None:
        super().__init__(delay=0.001)
        self.requests: list[tuple[str, str, str | None]] = []

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.requests.append((text, language, voice))
        async for pcm in super().synthesize(text, language=language, voice=voice):
            yield pcm


def heard(text: str, language: str | None) -> TranscriptEvent:
    return TranscriptEvent(text, is_final=True, language=language)


async def test_auto_language_speaks_each_turn_in_the_detected_language(harness: Harness) -> None:
    stt, tts = ControlledSTT(), RecordingTTS()
    llm = FakeLanguageModel("Namaste. Kaise ho.", delay=0.001)
    user = UserContext(user_id="u1", language="auto", voice="Girish")
    engine, sink = harness.build(stt=stt, tts=tts, llm=llm, user=user, settings=settings())
    await engine.start()

    stt.streams[0].push(heard("வணக்கம்", "ta-IN"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 1)
    await engine.on_playback_finished(1)
    await sink.wait_for_state(S.LISTENING, 2)
    stt.streams[0].push(heard("kaise ho", "hi-IN"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)

    spoken = [(lang, voice) for _, lang, voice in tts.requests]
    ta = ("ta-IN", resolve_voice("Girish", "ta-IN"))
    hi = ("hi-IN", resolve_voice("Girish", "hi-IN"))
    assert ta != hi and set(spoken) == {ta, hi}
    assert spoken == sorted(spoken, key=lambda r: r != ta)  # all of turn one, then turn two
    assert "Reply in Tamil (ta-IN)." in llm.calls[0][0].content
    assert "Reply in Hindi (hi-IN)." in llm.calls[1][0].content
    assert [t.language for t in sink.of_type(TranscriptFinal)] == ["ta-IN", "hi-IN"]


async def test_auto_language_falls_back_to_the_last_language_then_hindi(
    harness: Harness,
) -> None:
    stt, tts = ControlledSTT(), RecordingTTS()
    user = UserContext(user_id="u1", language="auto")
    engine, sink = harness.build(
        stt=stt, tts=tts, llm=FakeLanguageModel("Ji.", delay=0.001), user=user, settings=settings()
    )
    await engine.start()
    stt.streams[0].push(heard("hello", None))
    await sink.wait_for(is_a(TurnCompleted))
    assert tts.requests[0][1] == "hi-IN"

    await engine.on_playback_finished(1)
    await sink.wait_for_state(S.LISTENING, 2)
    stt.streams[0].push(heard("vanakkam", "ta-IN"))
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)
    await engine.on_playback_finished(2)
    await sink.wait_for_state(S.LISTENING, 3)
    stt.streams[0].push(heard("mm", None))  # detection failed: stay in Tamil
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 3)
    assert tts.requests[-1][1] == "ta-IN"


async def test_a_fixed_language_user_gets_no_language_note_and_their_voice(
    harness: Harness,
) -> None:
    stt, tts = ControlledSTT(), RecordingTTS()
    llm = FakeLanguageModel("Okay.", delay=0.001)
    user = UserContext(user_id="u1", language="en-IN", voice="Devika")
    engine, sink = harness.build(stt=stt, tts=tts, llm=llm, user=user, settings=settings())
    await engine.start()
    stt.streams[0].push(heard("hello there", "hi-IN"))  # detection is ignored for a fixed language
    await sink.wait_for(is_a(TurnCompleted))

    assert tts.requests == [("Okay.", "en-IN", "Devika")]
    assert "Reply in" not in llm.calls[0][0].content


async def test_typed_text_in_auto_mode_gets_no_language_note(harness: Harness) -> None:
    llm = FakeLanguageModel("Okay.")
    user = UserContext(user_id="u1", language="auto")
    engine, sink = harness.build(mode="text", llm=llm, user=user)
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for(is_a(TurnCompleted))
    assert "Reply in" not in llm.calls[0][0].content


# ---------------------------------------------------------------- captions


async def test_caption_events_bracket_each_segments_audio_with_exact_sample_counts(
    harness: Harness,
) -> None:
    engine, sink = harness.build(
        tts=FakeTextToSpeech(delay=0.001),
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001),
        settings=settings(),
    )
    await engine.start()
    await engine.on_speech_started()
    await engine.on_audio(b"\x00" * 640)
    await engine.on_speech_ended()
    await sink.wait_for(is_a(TurnCompleted))

    starts, ends = sink.of_type(AudioSegment), sink.of_type(AudioSegmentDone)
    assert [s.index for s in starts] == [0, 1, 2, 3] == [e.index for e in ends]
    assert [s.text for s in starts] == [
        "One two three.",
        "Four five six.",
        "Seven eight nine.",
        "Ten eleven twelve.",
    ]
    # FakeTTS: one 20 ms frame (320 samples) per word, three words per segment
    assert [e.samples for e in ends] == [960] * 4
    assert [s.first_seq for s in starts] == [0, 3, 6, 9]

    timeline = sink.timeline
    for start, end in zip(starts, ends, strict=True):
        a, b = timeline.index(start), timeline.index(end)
        between = [i for i in timeline[a + 1 : b] if isinstance(i, AudioFrame)]
        assert [f.seq for f in between] == list(range(start.first_seq, start.first_seq + 3))
        assert isinstance(timeline[a + 1], AudioFrame)  # announced right before its first chunk
        assert isinstance(timeline[b - 1], AudioFrame)  # closed right after its last
    assert sum(e.samples for e in ends) * 2 == sum(len(f.pcm) for f in sink.frames)


async def test_caption_events_carry_the_generation_and_stop_with_a_cancelled_turn(
    harness: Harness,
) -> None:
    engine, sink = harness.build(
        tts=FakeTextToSpeech(delay=SLOW_TTS), llm=FakeLanguageModel(LONG_REPLY, delay=0.001)
    )
    await engine.start()
    await engine.on_speech_started()
    await engine.on_speech_ended()
    await sink.wait_for(is_a(AudioSegment))
    await engine.on_cancel(1)
    cut = sink.index(is_a(TurnCancelled))
    await asyncio.sleep(0.15)

    assert all(s.generation_id == 1 for s in sink.of_type(AudioSegment))
    late = [
        i
        for i in sink.timeline[cut:]
        if isinstance(i, AudioSegment | AudioSegmentDone | AudioFrame)
    ]
    assert late == []  # nothing of the cancelled generation leaves after turn.cancelled


async def test_a_segment_that_makes_no_sound_is_never_announced(harness: Harness) -> None:
    class SilentFirst(FakeTextToSpeech):
        async def synthesize(
            self, text: str, *, language: str, voice: str | None = None
        ) -> AsyncIterator[bytes]:
            if text.startswith("One"):
                return
            async for pcm in super().synthesize(text, language=language, voice=voice):
                yield pcm

    engine, sink = harness.build(
        tts=SilentFirst(delay=0.001), llm=FakeLanguageModel(LONG_REPLY, delay=0.001)
    )
    await engine.start()
    await engine.on_speech_started()
    await engine.on_speech_ended()
    await sink.wait_for(is_a(TurnCompleted))

    assert [s.index for s in sink.of_type(AudioSegment)] == [1, 2, 3]
    assert [e.index for e in sink.of_type(AudioSegmentDone)] == [1, 2, 3]


async def test_caption_order_holds_when_tts_finishes_segments_out_of_pace(
    harness: Harness,
) -> None:
    engine, sink = harness.build(tts=TaggedTTS(), llm=FakeLanguageModel(LONG_REPLY, 0.001))
    await engine.start()
    await engine.on_speech_started()
    await engine.on_speech_ended()
    await sink.wait_for(is_a(TurnCompleted))

    markers = [
        (type(i).__name__, getattr(i, "index", None))
        for i in sink.timeline
        if isinstance(i, AudioSegment | AudioSegmentDone)
    ]
    assert markers == [(kind, n) for n in range(4) for kind in ("AudioSegment", "AudioSegmentDone")]


def test_module_exposes_no_interrupted_marker() -> None:
    import app.conversation.prompts as prompts

    assert not hasattr(prompts, "INTERRUPTED_MARKER")
    assert not hasattr(engine_module, "INTERRUPTED_MARKER")
