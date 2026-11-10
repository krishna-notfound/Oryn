"""VoiceConversation behaviour, driven end to end through fake providers."""

from __future__ import annotations

import asyncio

import pytest
from support import (
    LONG_REPLY,
    TWO_SENTENCES,
    AudioFrame,
    ControlledSTT,
    FailingLLM,
    FailingTTS,
    FixedMemory,
    Harness,
    ScriptedLLM,
    ScriptedTools,
    TaggedTTS,
    final,
    is_a,
)

import app.conversation.engine as engine_module
import app.conversation.pipeline as pipeline_module
import app.conversation.stt_link as stt_link_module
from app.config import Settings
from app.conversation.engine import VoiceConversation
from app.conversation.ports import UserContext
from app.providers.base import ChatMessage, LLMChunk, ProviderError, SpeechStarted, ToolCall
from app.providers.fakes import FakeLanguageModel, FakeSpeechToText, FakeTextToSpeech
from app.voice.state import VoiceState
from app.websocket.protocol import (
    AudioStarted,
    PipelineError,
    StateChanged,
    TextDelta,
    TranscriptFinal,
    TurnCancelled,
    TurnCompleted,
    TurnMetrics,
)

S = VoiceState


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A hand-set clock for the engine and pipeline, to test what was heard."""
    now = [100.0]
    monkeypatch.setattr(engine_module, "perf_counter", lambda: now[0])
    monkeypatch.setattr(pipeline_module, "perf_counter", lambda: now[0])
    return now


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_link_module, "REOPEN_BACKOFF_S", 0.01)


async def speak_one_turn(engine: VoiceConversation) -> None:
    await engine.start()
    await engine.on_speech_started()
    await engine.on_audio(b"\x00" * 640)
    await engine.on_speech_ended()


# ---------------------------------------------------------------- the happy paths


async def test_voice_turn_emits_events_in_order(harness: Harness) -> None:
    engine, sink = harness.build()
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_started(1)
    await engine.on_playback_finished(1)
    await sink.wait_for(is_a(TurnMetrics))

    assert sink.states == [S.LISTENING, S.PROCESSING, S.GENERATING, S.SPEAKING, S.LISTENING]
    order = [
        sink.index(is_a(TranscriptFinal)),
        sink.index(lambda i: isinstance(i, StateChanged) and i.state is S.PROCESSING),
        sink.index(lambda i: isinstance(i, StateChanged) and i.state is S.GENERATING),
        sink.index(is_a(TextDelta)),
        sink.index(is_a(AudioStarted)),
        sink.index(lambda i: isinstance(i, StateChanged) and i.state is S.SPEAKING),
        sink.index(is_a(AudioFrame)),
        sink.index(is_a(TurnCompleted)),
        sink.index(is_a(TurnMetrics)),
    ]
    assert order == sorted(order) and len(set(order)) == len(order)

    final_event = sink.of_type(TranscriptFinal)[0]
    assert (final_event.turn_id, final_event.text) == (1, TWO_SENTENCES)
    assert sink.of_type(AudioStarted)[0].sample_rate == 16_000
    assert sink.of_type(TurnCompleted)[0].text == TWO_SENTENCES
    assert "".join(t.delta for t in sink.of_type(TextDelta)).strip() == TWO_SENTENCES
    assert sink.frames and {f.generation_id for f in sink.frames} == {1}
    assert len(sink.frames) == 6  # one 20 ms frame per word, none dropped or duplicated


async def test_voice_turn_reports_metrics_once(harness: Harness) -> None:
    engine, sink = harness.build()
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_started(1)
    await engine.on_playback_finished(1)

    (metrics,) = sink.of_type(TurnMetrics)
    assert (metrics.turn_id, metrics.generation_id) == (1, 1)
    assert metrics.stt_ms is not None
    assert metrics.llm_ttft_ms is not None and metrics.tts_ttfb_ms is not None
    assert metrics.speech_end_to_first_audio_ms is not None
    assert metrics.interrupt_to_stop_ms is None


async def test_speech_end_mark_does_not_leak_into_the_next_utterance(harness: Harness) -> None:
    engine, _ = harness.build()
    await engine.start()
    await engine.on_speech_ended()  # e.g. a VAD blip that never produced a transcript
    await engine.on_speech_started()  # the next utterance begins
    assert engine._speech_end is None


async def test_playback_finished_before_done_completes_the_turn_once_done(
    harness: Harness,
) -> None:
    engine, sink = harness.build(llm=FakeLanguageModel(LONG_REPLY, delay=0.01))
    await speak_one_turn(engine)
    await sink.wait_for(is_a(AudioFrame))
    await engine.on_playback_finished(1)
    assert sink.states[-1] is S.SPEAKING

    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_finished(1)
    assert sink.states[-1] is S.LISTENING


async def test_text_mode_turn_sends_no_audio_and_returns_to_listening(harness: Harness) -> None:
    engine, sink = harness.build(mode="text")
    await engine.start()
    await engine.on_text("hello there")
    await sink.wait_for(is_a(TurnMetrics))

    assert sink.frames == []
    assert not sink.of_type(AudioStarted)
    assert sink.states == [S.LISTENING, S.PROCESSING, S.GENERATING, S.LISTENING]
    assert sink.of_type(TurnCompleted)[0].text == TWO_SENTENCES
    assert sink.index(is_a(TurnCompleted)) < sink.index(is_a(TurnMetrics))


async def test_generation_and_turn_ids_increase_per_turn(harness: Harness) -> None:
    engine, sink = harness.build(mode="text")
    await engine.start()
    await engine.on_text("one")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("two")
    await sink.wait_for_state(S.LISTENING, 3)

    assert [m.generation_id for m in sink.of_type(TurnCompleted)] == [1, 2]
    assert [m.turn_id for m in sink.of_type(TurnMetrics)] == [1, 2]


async def test_history_reaches_the_next_prompt(harness: Harness) -> None:
    llm = FakeLanguageModel("Sure thing.")
    engine, sink = harness.build(mode="text", llm=llm)
    await engine.start()
    await engine.on_text("first question")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("second question")
    await sink.wait_for_state(S.LISTENING, 3)

    roles = [(m.role, m.content) for m in llm.calls[1]]
    assert roles[1:] == [
        ("user", "first question"),
        ("assistant", "Sure thing."),
        ("user", "second question"),
    ]


# ---------------------------------------------------------------- TTS pipeline


async def test_sentences_are_synthesised_ahead_but_emitted_in_order(harness: Harness) -> None:
    tts = TaggedTTS()
    engine, sink = harness.build(mode="voice", tts=tts, llm=FakeLanguageModel(LONG_REPLY, 0.001))
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))

    spoken = b" ".join(f.pcm for f in sink.frames).decode()
    assert spoken == "One two three. Four five six. Seven eight nine. Ten eleven twelve."
    assert tts.max_active == 1  # one Timbre request at a time, the next starts at once
    assert len(tts.started) == 4


# ---------------------------------------------------------------- barge-in


async def test_interrupt_mid_speech_stops_audio_for_the_old_generation(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001), tts=FakeTextToSpeech(delay=0.03)
    )
    await speak_one_turn(engine)
    await sink.wait_for(is_a(AudioFrame))

    await engine.on_cancel(1)
    frames_at_interrupt = len(sink.frames)
    await asyncio.sleep(0.05)

    assert len(sink.frames) == frames_at_interrupt
    assert [e.generation_id for e in sink.of_type(TurnCancelled)] == [1]
    assert sink.states[-3:] == [S.SPEAKING, S.INTERRUPTED, S.LISTENING]
    assert sink.index(is_a(TurnCancelled)) < sink.index(lambda i: isinstance(i, TurnMetrics))
    (metrics,) = sink.of_type(TurnMetrics)
    assert metrics.interrupt_to_stop_ms is not None


async def test_interrupt_is_idempotent_and_safe_when_idle(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001), tts=FakeTextToSpeech(delay=0.03)
    )
    await engine.start()
    await engine.on_speech_started()  # nothing in flight
    assert not sink.of_type(TurnCancelled)
    assert not engine._arbiter.contested

    await engine.on_audio(b"\x00" * 640)
    await engine.on_speech_ended()
    await sink.wait_for(is_a(AudioFrame))
    await asyncio.gather(engine.on_cancel(1), engine.on_cancel(1), engine.on_cancel(1))

    assert len(sink.of_type(TurnCancelled)) == 1
    assert len(sink.of_type(TurnMetrics)) == 1
    assert sink.states[-1] is S.LISTENING


async def test_next_turn_after_barge_in_gets_a_new_generation(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001), tts=FakeTextToSpeech(delay=0.03)
    )
    await speak_one_turn(engine)
    await sink.wait_for(is_a(AudioFrame))
    await engine.on_cancel(1)
    old_frames = len(sink.frames)

    await engine.on_text("tell me more")
    await sink.wait_for(lambda i: isinstance(i, AudioFrame) and i.generation_id == 2)

    assert all(f.generation_id == 1 for f in sink.frames[:old_frames])
    assert all(f.generation_id == 2 for f in sink.frames[old_frames:])
    assert sink.of_type(TextDelta)[-1].generation_id == 2


async def test_reply_cut_off_before_a_word_was_heard_leaves_no_trace(harness: Harness) -> None:
    llm = FakeLanguageModel(LONG_REPLY, delay=0.001)
    engine, sink = harness.build(llm=llm, tts=FakeTextToSpeech(delay=0.03))
    await speak_one_turn(engine)
    await sink.wait_for(is_a(AudioFrame))
    await engine.on_cancel(1)
    await engine.on_text("sorry, go on")
    await sink.wait_for(is_a(TurnCompleted))

    history = llm.calls[1][1:]
    assert [(m.role, m.content) for m in history[:-1]] == [("user", TWO_SENTENCES)]
    assert "cut off" not in llm.calls[1][0].content  # nothing heard, nothing to remind it of


async def test_history_keeps_only_sentences_that_finished_playing(
    harness: Harness, clock: list[float]
) -> None:
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(llm=llm)
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))  # all 120 ms of audio has been sent

    await engine.on_playback_started(1)
    clock[0] = 100.08  # first sentence (60 ms) played, second (ends at 120 ms) not
    await engine.on_speech_started()
    await engine.on_text("go on")
    await sink.wait_for_state(S.GENERATING, 2)

    assert llm.calls[1][1].role == "user"
    assert llm.calls[1][2].content == "Alpha beta gamma."  # heard text only, no marker
    assert 'cut off while saying: "Alpha beta gamma."' in llm.calls[1][0].content


async def test_full_playback_then_late_interrupt_is_not_marked_cut_off(
    harness: Harness, clock: list[float]
) -> None:
    llm = FakeLanguageModel(TWO_SENTENCES, delay=0.001)
    engine, sink = harness.build(llm=llm)
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))

    clock[0] = 105.0  # long after the audio ended, browser never sent playback.finished
    await engine.on_text("next")
    await sink.wait_for_state(S.GENERATING, 2)

    assert llm.calls[1][2].content == TWO_SENTENCES


async def test_server_side_speech_start_during_speaking_only_contests_the_turn(
    harness: Harness,
) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(
        stt=stt,
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001),
        tts=FakeTextToSpeech(delay=0.03),
    )
    await engine.start()
    stt.streams[0].push(final("tell me a story"))
    await sink.wait_for_state(S.SPEAKING)

    stt.streams[0].push(SpeechStarted())
    frames = len(sink.frames)
    await sink.wait_for(lambda i: isinstance(i, AudioFrame) and i.seq > frames)  # still talking

    assert not sink.of_type(TurnCancelled)
    assert engine._arbiter.contested


async def test_speech_start_while_listening_is_not_an_interrupt(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()
    stt.streams[0].push(SpeechStarted())
    await asyncio.sleep(0.02)
    assert not sink.of_type(TurnCancelled)
    assert sink.states == [S.LISTENING]


async def test_new_final_transcript_interrupts_the_reply_in_flight(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(
        stt=stt,
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001),
        tts=FakeTextToSpeech(delay=0.03),
    )
    await engine.start()
    stt.streams[0].push(final("first"))
    await sink.wait_for(is_a(AudioFrame))
    stt.streams[0].push(final("second"))
    await sink.wait_for(lambda i: isinstance(i, AudioFrame) and i.generation_id == 2)

    assert [t.turn_id for t in sink.of_type(TranscriptFinal)] == [1, 2]
    assert sink.index(is_a(TurnCancelled)) < sink.index(
        lambda i: isinstance(i, AudioFrame) and i.generation_id == 2
    )
    interrupted_at = sink.index(is_a(TurnCancelled))
    assert not [
        f
        for f in sink.timeline[interrupted_at:]
        if isinstance(f, AudioFrame) and f.generation_id == 1
    ]


async def test_audio_is_forwarded_to_the_stt_stream(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, _ = harness.build(stt=stt)
    await engine.start()
    await engine.on_audio(b"abc")
    await engine.on_audio(b"def")
    await engine.on_speech_ended()
    await asyncio.sleep(0.01)  # the pump task delivers in order
    assert stt.streams[0].audio == [b"abc", b"def"]
    assert stt.streams[0].finished


async def test_stream_that_ends_after_finish_is_reopened_for_the_next_utterance(
    harness: Harness,
) -> None:
    engine, sink = harness.build(stt=FakeSpeechToText("hello oryn", delay=0.001))
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))
    await engine.on_playback_finished(1)

    await engine.on_audio(b"\x00" * 640)
    await engine.on_speech_ended()
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)


# ---------------------------------------------------------------- tools


def tool_call(n: int) -> LLMChunk:
    return LLMChunk(tool_calls=(ToolCall(f"c{n}", "weather", {"city": "Pune"}),))


async def test_tool_call_loop_runs_tool_and_streams_again(harness: Harness) -> None:
    llm = ScriptedLLM(
        [
            [LLMChunk(text="Let me check. "), tool_call(1)],
            [LLMChunk(text="It is warm today. ")],
        ]
    )
    tools = ScriptedTools()
    engine, sink = harness.build(llm=llm, tools=tools)
    await speak_one_turn(engine)
    await sink.wait_for(is_a(TurnCompleted))

    assert [c.name for c in tools.runs] == ["weather"]
    second = llm.calls[1]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].id == "c1"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert llm.tool_specs[0] and llm.tool_specs[1]
    spoken = sink.of_type(TurnCompleted)[0].text
    assert spoken == "Let me check. It is warm today."
    assert "SECRET-TOOL-RESULT" not in spoken
    assert all(b"SECRET" not in f.pcm for f in sink.frames)


async def test_tool_calls_are_capped_per_turn(harness: Harness) -> None:
    llm = ScriptedLLM(
        [[tool_call(1), tool_call(2)], [tool_call(3), tool_call(4)], [tool_call(5)], [tool_call(6)]]
    )
    tools = ScriptedTools()
    settings = Settings(_env_file=None, max_tool_calls_per_turn=3)
    engine, sink = harness.build(mode="text", llm=llm, tools=tools, settings=settings)
    await engine.start()
    await engine.on_text("weather?")
    await sink.wait_for(is_a(TurnCompleted))

    assert len(tools.runs) == 3
    assert len(llm.calls) == 3  # third round was given no tools, so the loop ended
    assert llm.tool_specs[2] == []
    assert sink.states[-1] is S.LISTENING


# ---------------------------------------------------------------- memory and safety


async def test_memories_are_added_to_the_prompt_when_enabled(harness: Harness) -> None:
    llm = FakeLanguageModel("Nice.")
    user = UserContext(user_id="u1", memory_enabled=True)
    engine, sink = harness.build(mode="text", llm=llm, user=user, memory=FixedMemory())
    await engine.start()
    await engine.on_text("hi")
    await sink.wait_for(is_a(TurnCompleted))
    assert "Bruno" in llm.calls[0][0].content


async def test_memories_ignored_without_consent(harness: Harness) -> None:
    llm = FakeLanguageModel("Nice.")
    engine, sink = harness.build(mode="text", llm=llm, memory=FixedMemory())
    await engine.start()
    await engine.on_text("hi")
    await sink.wait_for(is_a(TurnCompleted))
    assert "Bruno" not in llm.calls[0][0].content


async def test_slow_memory_is_skipped_not_waited_for(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "MEMORY_BUDGET_S", 0.02)
    llm = FakeLanguageModel("Nice.")
    user = UserContext(user_id="u1", memory_enabled=True)
    engine, sink = harness.build(mode="text", llm=llm, user=user, memory=FixedMemory(delay=5))
    await engine.start()
    await engine.on_text("hi")
    await sink.wait_for(is_a(TurnCompleted))
    assert "Bruno" not in llm.calls[0][0].content


async def test_crisis_language_adds_the_safety_instruction_for_that_turn_only(
    harness: Harness,
) -> None:
    llm = FakeLanguageModel("I am here with you.")
    engine, sink = harness.build(mode="text", llm=llm)
    await engine.start()
    await engine.on_text("I want to kill myself")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("thanks for listening")
    await sink.wait_for_state(S.LISTENING, 3)

    assert "14416" in llm.calls[0][0].content
    assert "14416" not in llm.calls[1][0].content


# ---------------------------------------------------------------- hooks


async def test_hooks_receive_the_finished_exchange(harness: Harness) -> None:
    messages: list[tuple[str, str, bool]] = []
    after: list[list[ChatMessage]] = []

    async def on_message(role: str, text: str, interrupted: bool) -> None:
        messages.append((role, text, interrupted))

    async def after_turn(user: UserContext, history: list[ChatMessage]) -> None:
        after.append(history)

    engine, sink = harness.build(mode="text", on_message=on_message, after_turn=after_turn)
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for(is_a(TurnMetrics))
    await engine.close()  # lets hooks finish

    assert messages == [("user", "hello", False), ("assistant", TWO_SENTENCES, False)]
    assert [m.content for m in after[0]] == ["hello", TWO_SENTENCES]


async def test_interrupted_assistant_message_is_persisted_as_interrupted(
    harness: Harness,
) -> None:
    messages: list[tuple[str, str, bool]] = []

    async def on_message(role: str, text: str, interrupted: bool) -> None:
        messages.append((role, text, interrupted))

    engine, sink = harness.build(
        on_message=on_message,
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001),
        tts=FakeTextToSpeech(delay=0.03),
    )
    await speak_one_turn(engine)
    await sink.wait_for(is_a(AudioFrame))
    await engine.on_speech_started()
    await engine.close()

    assert messages[0] == ("user", TWO_SENTENCES, False)
    assert all(m[2] for m in messages[1:])


async def test_hook_failures_never_break_the_turn(harness: Harness) -> None:
    async def broken_message(role: str, text: str, interrupted: bool) -> None:
        raise RuntimeError("db down")

    async def broken_after(user: UserContext, history: list[ChatMessage]) -> None:
        raise RuntimeError("extraction failed")

    engine, sink = harness.build(mode="text", on_message=broken_message, after_turn=broken_after)
    await engine.start()
    await engine.on_text("one")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("two")
    await sink.wait_for_state(S.LISTENING, 3)
    await engine.close()

    assert not sink.of_type(PipelineError)


async def test_slow_hooks_do_not_delay_the_reply(harness: Harness) -> None:
    release = asyncio.Event()

    async def slow_after(user: UserContext, history: list[ChatMessage]) -> None:
        await release.wait()

    engine, sink = harness.build(mode="text", after_turn=slow_after)
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for(is_a(TurnMetrics))
    assert sink.states[-1] is S.LISTENING
    release.set()


# ---------------------------------------------------------------- provider errors


async def test_llm_error_emits_error_event_and_recovers(harness: Harness) -> None:
    llm = FailingLLM(ProviderError("sarvam", "503", retryable=True))
    engine, sink = harness.build(mode="text", llm=llm)
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for_state(S.LISTENING, 2)

    (error,) = sink.of_type(PipelineError)
    assert (error.code, error.retryable) == ("provider_error", True)
    assert "sarvam" in error.message and "503" not in error.message
    assert sink.states[-1] is S.LISTENING

    # the session is still usable
    harness.engines[0]._llm = FakeLanguageModel("Back again.")  # noqa: SLF001
    await engine.on_text("hello again")
    await sink.wait_for(is_a(TurnCompleted))


async def test_tts_error_mid_turn_emits_error_and_returns_to_listening(harness: Harness) -> None:
    engine, sink = harness.build(tts=FailingTTS())
    await speak_one_turn(engine)
    await sink.wait_for_state(S.LISTENING, 2)

    (error,) = sink.of_type(PipelineError)
    assert error.code == "provider_error" and "timbre" in error.message
    assert not sink.of_type(TurnCompleted)


async def test_unexpected_exception_becomes_internal_error(harness: Harness) -> None:
    engine, sink = harness.build(mode="text", llm=FailingLLM(ValueError("bug")))
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for_state(S.LISTENING, 2)
    (error,) = sink.of_type(PipelineError)
    assert (error.code, error.retryable) == ("internal_error", False)
    assert "bug" not in error.message


async def test_retryable_stt_error_reopens_the_stream_once(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()
    stt.streams[0].push(ProviderError("prisma", "socket dropped", retryable=True))
    await sink.wait_for_state(S.LISTENING, 2)

    assert len(stt.streams) == 2 and stt.streams[0].closed
    assert sink.states == [S.LISTENING, S.RECONNECTING, S.LISTENING]
    assert sink.of_type(PipelineError)[0].retryable

    await engine.on_audio(b"abc")
    await asyncio.sleep(0.01)
    assert stt.streams[1].audio == [b"abc"]
    stt.streams[1].push(final("still works"))
    await sink.wait_for(is_a(TurnCompleted))


async def test_a_long_lived_stream_ending_is_rotated_without_telling_the_user(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider caps a session at 15 minutes: that is routine, not an error."""
    monkeypatch.setattr(stt_link_module, "STABLE_AFTER_S", 0.05)
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()
    await asyncio.sleep(0.08)
    stt.streams[0].push(ProviderError("prisma", "session limit", retryable=True))
    for _ in range(200):
        if len(stt.streams) == 2:
            break
        await asyncio.sleep(0.005)

    assert len(stt.streams) == 2 and stt.streams[0].closed
    assert sink.states == [S.LISTENING] and not sink.of_type(PipelineError)
    await engine.on_audio(b"abc")
    await asyncio.sleep(0.01)
    assert stt.streams[1].audio == [b"abc"]
    stt.streams[1].push(final("still here"))
    await sink.wait_for(is_a(TurnCompleted))


async def test_stt_error_when_reopen_fails_ends_in_error_state(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()
    stt.open_errors.append(ProviderError("prisma", "refused", retryable=True))
    stt.streams[0].push(ProviderError("prisma", "socket dropped", retryable=True))
    await sink.wait_for_state(S.ERROR)

    assert len(stt.streams) == 1
    assert len(sink.of_type(PipelineError)) == 2


async def test_non_retryable_stt_error_ends_in_error_state(harness: Harness) -> None:
    stt = ControlledSTT()
    engine, sink = harness.build(stt=stt)
    await engine.start()
    stt.streams[0].push(ProviderError("prisma", "bad api key", retryable=False))
    await sink.wait_for_state(S.ERROR)

    assert len(stt.streams) == 1
    await engine.on_audio(b"abc")  # ignored, no reconnect storm
    assert len(stt.streams) == 1


# ---------------------------------------------------------------- shutdown


async def test_close_cancels_everything_it_owns(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "HOOK_GRACE_S", 0.01)
    stt = ControlledSTT()
    engine, sink = harness.build(
        stt=stt,
        llm=FakeLanguageModel(LONG_REPLY, delay=0.001),
        tts=FakeTextToSpeech(delay=0.05),
        after_turn=lambda user, history: asyncio.sleep(10),
    )
    await engine.start()
    stt.streams[0].push(final("hello"))
    await sink.wait_for(is_a(AudioFrame))

    await engine.close()
    frames = len(sink.frames)
    await asyncio.sleep(0.05)

    others = asyncio.all_tasks() - {asyncio.current_task()}
    assert not others
    assert stt.streams[0].closed
    assert len(sink.frames) == frames


async def test_close_is_idempotent_and_ignores_later_input(harness: Harness) -> None:
    engine, sink = harness.build()
    await engine.start()
    await engine.close()
    await engine.close()
    events_before = len(sink.timeline)
    await engine.on_audio(b"abc")
    await engine.on_speech_started()
    assert len(sink.timeline) == events_before


async def test_close_without_start_is_safe(harness: Harness) -> None:
    engine, _ = harness.build()
    await engine.close()
