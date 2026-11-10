"""TurnManager and the staged turn pipeline: ordering, backpressure, cancellation, recovery.

Timing is scripted with test-local slow doubles rather than sleeps in the code under test."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence

import pytest
from support import (
    LONG_REPLY,
    AudioFrame,
    FixedMemory,
    Harness,
    RecordingSink,
    TaggedTTS,
    is_a,
)

import app.conversation.engine as engine_module
from app.conversation.engine import VoiceConversation
from app.conversation.pipeline import TurnOutbox, TurnPipeline
from app.conversation.ports import UserContext
from app.conversation.turns import Turn, TurnManager
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    LLMChunk,
    ProviderError,
    ToolSpec,
)
from app.providers.fakes import FakeLanguageModel, FakeTextToSpeech
from app.voice.latency import TurnTimer
from app.voice.state import VoiceState
from app.websocket.protocol import (
    PipelineError,
    ServerEvent,
    TurnCancelled,
    TurnCompleted,
    TurnMetrics,
)

S = VoiceState


# ---------------------------------------------------------------- doubles


class SlowLLM(LanguageModel):
    """Streams `words` one by one; `finished` is set only after the last one went out."""

    name = "slow-llm"
    model = "slow"

    def __init__(self, text: str, delay: float, first_token_delay: float = 0.0) -> None:
        self.words = text.split()
        self.delay = delay
        self.first_token_delay = first_token_delay
        self.finished = asyncio.Event()

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        await asyncio.sleep(self.first_token_delay)
        for word in self.words:
            await asyncio.sleep(self.delay)
            yield LLMChunk(text=word + " ")
        self.finished.set()
        yield LLMChunk(finish_reason="stop")


class WatchingTTS(FakeTextToSpeech):
    """Remembers, for each request, whether the LLM had already finished."""

    def __init__(self, llm: SlowLLM) -> None:
        super().__init__(delay=0.001)
        self._llm = llm
        self.llm_done_at_request: list[bool] = []

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.llm_done_at_request.append(self._llm.finished.is_set())
        async for pcm in super().synthesize(text, language=language, voice=voice):
            yield pcm


class FailsOnSecondSegment(FakeTextToSpeech):
    def __init__(self) -> None:
        super().__init__(delay=0.001)
        self.requests = 0
        self.healed = False

    async def synthesize(
        self, text: str, *, language: str, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.requests += 1
        if self.requests == 2 and not self.healed:
            raise ProviderError("timbre", "socket closed", retryable=True)
        async for pcm in super().synthesize(text, language=language, voice=voice):
            yield pcm


class SlowSink(RecordingSink):
    """A client socket that takes a while to accept each audio frame."""

    async def send_audio(self, generation_id: int, seq: int, pcm: bytes) -> None:
        await asyncio.sleep(0.005)
        await super().send_audio(generation_id, seq, pcm)


class GatedOutput:
    """TurnPipeline output whose audio writes block until `gate` opens (a stalled client)."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.audio_frames = 0

    async def event(self, turn: Turn, event: ServerEvent) -> None:
        pass

    async def state(self, turn: Turn, state: VoiceState) -> None:
        pass

    async def audio(self, turn: Turn, seq: int, pcm: bytes) -> None:
        await self.gate.wait()
        self.audio_frames += 1


def new_turn(*, confirmed: bool = True) -> Turn:
    turn = Turn(1, 1, "hello", TurnTimer(), speculative=not confirmed)
    if confirmed:
        turn.confirmed.set()
    turn.generating.set()
    return turn


async def speak(engine: VoiceConversation) -> None:
    await engine.on_speech_started()
    await engine.on_audio(b"\x00" * 640)
    await engine.on_speech_ended()


def no_stray_tasks() -> bool:
    return not asyncio.all_tasks() - {asyncio.current_task()}


# ---------------------------------------------------------------- pipeline


async def test_tts_starts_before_llm_finishes(harness: Harness) -> None:
    llm = SlowLLM(LONG_REPLY, delay=0.02)
    tts = WatchingTTS(llm)
    engine, sink = harness.build(llm=llm, tts=tts)
    await engine.start()
    await speak(engine)
    await sink.wait_for(is_a(TurnCompleted))

    assert tts.llm_done_at_request[0] is False  # the first segment was synthesised mid-reply
    assert len(tts.llm_done_at_request) == 4


async def test_audio_delivered_in_segment_order(harness: Harness) -> None:
    engine, sink = harness.build(tts=TaggedTTS(), llm=FakeLanguageModel(LONG_REPLY, 0.001))
    await engine.start()
    await speak(engine)
    await sink.wait_for(is_a(TurnCompleted))

    spoken = b" ".join(f.pcm for f in sink.frames).decode()
    assert spoken == "One two three. Four five six. Seven eight nine. Ten eleven twelve."
    assert [f.seq for f in sink.frames] == list(range(len(sink.frames)))  # per-generation index
    assert {f.generation_id for f in sink.frames} == {1}


async def test_slow_sink_applies_backpressure() -> None:
    consumed = 0

    async def endless_reply() -> AsyncGenerator[str, None]:
        nonlocal consumed
        while True:
            consumed += 1
            yield "word "
            await asyncio.sleep(0)

    output = GatedOutput()
    pipeline = TurnPipeline(output, FakeTextToSpeech(delay=0), language="en-IN", voice=None)
    run = asyncio.create_task(pipeline.run(new_turn(), endless_reply(), TurnOutbox()))

    await asyncio.sleep(0.3)
    stalled_at = consumed
    await asyncio.sleep(0.3)
    assert consumed == stalled_at  # the LLM read paused: nothing is being buffered any more
    assert stalled_at < 1_000  # ...and it paused after a bounded amount of read-ahead

    output.gate.set()
    await asyncio.sleep(0.2)
    assert consumed > stalled_at and output.audio_frames > 0  # releasing the client resumes it

    run.cancel()
    await asyncio.wait({run})
    assert no_stray_tasks()


# ---------------------------------------------------------------- cancellation


async def test_cancel_during_processing(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "MEMORY_BUDGET_S", 5.0)
    llm = FakeLanguageModel("never spoken")
    user = UserContext(user_id="u1", memory_enabled=True)
    engine, sink = harness.build(user=user, memory=FixedMemory(delay=1), llm=llm)
    await engine.start()
    await engine.on_text("hello")
    await sink.wait_for_state(S.PROCESSING)

    await engine.on_cancel(1)

    assert llm.calls == []  # cancelled while still recalling memories
    assert [c.generation_id for c in sink.of_type(TurnCancelled)] == [1]
    assert sink.states[-3:] == [S.PROCESSING, S.INTERRUPTED, S.LISTENING]


async def test_cancel_during_generating(harness: Harness) -> None:
    llm = SlowLLM("late reply", delay=0.01, first_token_delay=5)
    engine, sink = harness.build(llm=llm)
    await engine.start()
    await speak(engine)
    await sink.wait_for_state(S.GENERATING)

    await engine.on_cancel(1)

    assert sink.frames == []
    (cancelled,) = sink.of_type(TurnCancelled)
    assert (cancelled.reason, cancelled.heard_text) == ("client_cancel", "")
    assert sink.states[-3:] == [S.GENERATING, S.INTERRUPTED, S.LISTENING]


async def test_cancel_during_speaking(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, 0.001), tts=FakeTextToSpeech(delay=0.03)
    )
    await engine.start()
    await speak(engine)
    await sink.wait_for_state(S.SPEAKING)

    await engine.on_cancel(1)

    assert sink.states[-3:] == [S.SPEAKING, S.INTERRUPTED, S.LISTENING]
    assert len(sink.of_type(TurnCancelled)) == 1
    assert sink.of_type(TurnMetrics)[0].interrupt_to_stop_ms is not None


async def test_stale_cancel_is_ignored(harness: Harness) -> None:
    llm = SlowLLM("second reply goes on for a while yet", delay=0.02)
    engine, sink = harness.build(mode="text", llm=llm)
    await engine.start()
    await engine.on_text("first")
    await sink.wait_for_state(S.LISTENING, 2)
    await engine.on_text("second")  # generation 2 is live
    await sink.wait_for_state(S.GENERATING, 2)

    await engine.on_cancel(1)  # the browser is still answering generation 1

    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)
    assert not sink.of_type(TurnCancelled)


async def test_repeated_cancel_is_idempotent(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, 0.001), tts=FakeTextToSpeech(delay=0.03)
    )
    await engine.start()
    await speak(engine)
    await sink.wait_for(is_a(AudioFrame))

    await asyncio.gather(*(engine.on_cancel(1) for _ in range(5)))
    await engine.on_cancel(1)

    assert len(sink.of_type(TurnCancelled)) == 1
    assert len(sink.of_type(TurnMetrics)) == 1
    assert sink.states[-1] is S.LISTENING


async def test_no_audio_after_cancel_returns(harness: Harness) -> None:
    sink = SlowSink()
    engine, _ = harness.build(llm=FakeLanguageModel(LONG_REPLY, 0.001))
    engine._sink = sink  # noqa: SLF001 - a sink that makes every write take a while
    engine._output._sink = sink  # noqa: SLF001
    await engine.start()
    await speak(engine)
    await sink.wait_for(lambda i: isinstance(i, AudioFrame) and i.seq >= 3)

    await engine.on_cancel(1)
    at_return = len(sink.frames)
    cancelled_at = sink.index(is_a(TurnCancelled))
    await asyncio.sleep(0.2)

    assert len(sink.frames) == at_return
    assert not [i for i in sink.timeline[cancelled_at:] if isinstance(i, AudioFrame)]


async def test_tts_failure_mid_reply_recovers(harness: Harness) -> None:
    tts = FailsOnSecondSegment()
    engine, sink = harness.build(tts=tts, llm=FakeLanguageModel(LONG_REPLY, 0.001))
    await engine.start()
    await speak(engine)
    await sink.wait_for_state(S.LISTENING, 2)

    (error,) = sink.of_type(PipelineError)
    assert (error.code, error.retryable, error.generation_id) == ("provider_error", True, 1)
    (cancelled,) = sink.of_type(TurnCancelled)
    assert cancelled.reason == "error"
    assert sink.index(is_a(PipelineError)) < sink.index(is_a(TurnCancelled))
    assert sink.states[-3:] == [S.SPEAKING, S.INTERRUPTED, S.LISTENING]
    frames_after_failure = len(sink.frames)

    tts.healed = True  # the next turn works
    await speak(engine)
    await sink.wait_for(lambda i: isinstance(i, TurnCompleted) and i.generation_id == 2)
    assert len(sink.frames) > frames_after_failure


async def test_close_leaves_no_tasks(harness: Harness) -> None:
    engine, sink = harness.build(
        llm=FakeLanguageModel(LONG_REPLY, 0.001), tts=FakeTextToSpeech(delay=0.05)
    )
    await engine.start()
    await speak(engine)
    await sink.wait_for(is_a(AudioFrame))

    await engine.close()

    assert no_stray_tasks()


# ---------------------------------------------------------------- TurnManager on its own


class ManagerRig:
    def __init__(self, cleanup_s: float = 0.0) -> None:
        self.cancelled: list[tuple[int, str]] = []
        self.fence_during_cleanup: list[int | None] = []
        self._cleanup_s = cleanup_s
        self.manager = TurnManager(self._run, self._on_cancelled)

    async def _run(self, turn: Turn) -> None:
        try:
            await asyncio.sleep(60)
        finally:
            self.fence_during_cleanup.append(self.manager.live_generation)
            await asyncio.sleep(self._cleanup_s)

    async def _on_cancelled(self, turn: Turn, reason: str) -> None:
        self.cancelled.append((turn.generation_id, reason))


async def test_cancel_sets_the_fence_before_the_first_await() -> None:
    rig = ManagerRig(cleanup_s=0.05)
    turn = await rig.manager.begin("hi", speculative=False)
    assert rig.manager.live_generation == turn.generation_id
    await asyncio.sleep(0)  # let the turn start

    cancel = asyncio.create_task(rig.manager.cancel("test"))
    await asyncio.sleep(0)  # one loop step: cancel() ran up to its first suspension

    assert rig.manager.live_generation is None  # fenced while the old turn is still unwinding
    assert not turn.task.done()  # type: ignore[union-attr]
    assert await cancel is True
    assert rig.fence_during_cleanup == [None]
    assert rig.cancelled == [(1, "test")]


async def test_concurrent_cancels_end_the_turn_once() -> None:
    rig = ManagerRig(cleanup_s=0.02)
    await rig.manager.begin("hi", speculative=False)
    results = await asyncio.gather(*(rig.manager.cancel(f"r{i}") for i in range(4)))
    assert results.count(True) == 1 and len(rig.cancelled) == 1


async def test_cancel_with_a_stale_generation_id_changes_nothing() -> None:
    rig = ManagerRig()
    await rig.manager.begin("one", speculative=False)
    await rig.manager.cancel("done")
    second = await rig.manager.begin("two", speculative=False)

    assert await rig.manager.cancel("late", generation_id=1) is False
    assert rig.manager.live_generation == second.generation_id
    assert await rig.manager.cancel("now", generation_id=2) is True


async def test_begin_replaces_the_turn_in_flight_and_ids_keep_increasing() -> None:
    rig = ManagerRig()
    first = await rig.manager.begin("one", speculative=False)
    second = await rig.manager.begin("two", speculative=False)

    assert (first.turn_id, second.turn_id) == (1, 2)
    assert rig.cancelled == [(1, "superseded")]
    assert first.task is not None and first.task.done()
    await rig.manager.close()
    assert no_stray_tasks()


async def test_speculative_turn_confirms_itself_after_the_grace_period() -> None:
    rig = ManagerRig()
    turn = await rig.manager.begin("hi", speculative=True, grace_s=0.03)
    assert turn.speculative and not turn.confirmed.is_set()
    await asyncio.sleep(0.06)
    assert not turn.speculative and turn.confirmed.is_set()
    await rig.manager.close()
