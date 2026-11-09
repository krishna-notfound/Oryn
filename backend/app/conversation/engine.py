"""VoiceConversation: the orchestrator behind one live conversation.

Flow per turn: browser VAD says the user stopped (`on_speech_ended`) -> Prisma commit ->
final transcript -> a *speculative* turn starts at once (the LLM does not wait) -> memories +
crisis check -> context -> LLM -> segmenter -> Timbre -> audio frames tagged with a generation
id. The first audio is held behind a short gate; if the user speaks again before it opens the
turn is dropped silently and its words are merged into the next transcript.

Concurrency rules this module relies on:
  * `TurnManager` owns the active turn and the generation fence. Cancelling (barge-in,
    superseding, shutdown) clears the fence synchronously, before any await, so no frame of the
    old generation can leave afterwards. No lock is held across a provider call.
  * One turn = one TaskGroup (`TurnPipeline`); STT lives in its own supervised `SttLink`, so a
    stream reconnect never blocks the WebSocket receive loop or a cancel.
  * `CancelledError` is never swallowed; broad `except Exception` only where a failure must not
    take the call down.
Spec: docs/superpowers/specs/2026-11-09-realtime-pipeline-v2-design.md
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from time import perf_counter
from typing import Literal

from app.config import Settings
from app.conversation.context import ContextBuilder
from app.conversation.history import ConversationHistory
from app.conversation.pipeline import TurnOutbox, TurnPipeline
from app.conversation.ports import (
    Conversation,
    EventSink,
    Memory,
    MemoryRetriever,
    ToolRunner,
    UserContext,
)
from app.conversation.streams import closing
from app.conversation.stt_link import LinkStatus, SttLink
from app.conversation.turns import Turn, TurnManager
from app.persistence.conversations import StoredMessage
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    ProviderError,
    SpeechStarted,
    SpeechToText,
    STTEvent,
    TextToSpeech,
    ToolCall,
    TranscriptEvent,
)
from app.safety.crisis import CrisisDetector
from app.voice.latency import latency_stats
from app.voice.state import VoiceState, VoiceStateMachine
from app.websocket.protocol import (
    LlmStarted,
    PipelineError,
    ServerEvent,
    StateChanged,
    TranscriptFinal,
    TurnCancelled,
    TurnCompleted,
)

log = logging.getLogger(__name__)

MEMORY_BUDGET_S = 0.15  # recall is a bonus; never let it hold up the reply
TOOL_TIMEOUT_S = 8.0
HOOK_GRACE_S = 2.0  # how long close() lets persistence hooks finish before cancelling them
HISTORY_LOAD_TIMEOUT_S = 1.5
TTS_WARM_UP_TIMEOUT_S = 5.0
RESUMED_HOLD_S = 1.5  # words kept for a continuation that never produces a transcript

MessageHook = Callable[[str, str, bool], Awaitable[None]]
AfterTurnHook = Callable[[UserContext, list[ChatMessage]], Awaitable[None]]
HistoryLoader = Callable[[], Awaitable[Sequence[StoredMessage]]]


class _TurnOutput:
    """The pipeline's way out: every send is checked against the generation fence."""

    def __init__(self, sink: EventSink, state: VoiceStateMachine, turns: TurnManager) -> None:
        self._sink = sink
        self._state = state
        self._turns = turns

    async def event(self, turn: Turn, event: ServerEvent) -> None:
        if self._live(turn):
            stamped = event.model_copy(
                update={"turn_id": turn.turn_id, "generation_id": turn.generation_id}
            )
            await self._sink.send_event(stamped)

    async def state(self, turn: Turn, state: VoiceState) -> None:
        if self._live(turn):
            await self._state.transition(state)

    async def audio(self, turn: Turn, seq: int, pcm: bytes) -> None:
        if self._live(turn):
            await self._sink.send_audio(turn.generation_id, seq, pcm)

    def _live(self, turn: Turn) -> bool:
        return self._turns.live_generation == turn.generation_id


class VoiceConversation(Conversation):
    def __init__(
        self,
        *,
        user: UserContext,
        mode: Literal["voice", "text"],
        stt: SpeechToText,
        llm: LanguageModel,
        tts: TextToSpeech,
        sink: EventSink,
        settings: Settings,
        memory: MemoryRetriever | None = None,
        tools: ToolRunner | None = None,
        after_turn: AfterTurnHook | None = None,
        on_message: MessageHook | None = None,
        load_history: HistoryLoader | None = None,
    ) -> None:
        self._user = user
        self._mode = mode
        self._llm = llm
        self._tts = tts
        self._sink = sink
        self._settings = settings
        self._memory = memory
        self._tools = tools
        self._after_turn = after_turn
        self._on_message = on_message
        self._load_history = load_history

        self._state = VoiceStateMachine(self._announce)
        self._history = ConversationHistory(settings.context_max_turns)
        self._context = ContextBuilder(settings.context_max_turns)
        self._crisis = CrisisDetector()
        fmt = tts.output_format
        self._bytes_per_second = fmt.sample_rate * fmt.channels * 2

        self._turns = TurnManager(self._run_turn, self._on_turn_cancelled)
        self._output = _TurnOutput(sink, self._state, self._turns)
        self._pipeline = TurnPipeline(
            self._output,
            tts if mode == "voice" else None,
            language=user.language,
            voice=settings.gnani_tts_voice or None,
        )
        self._link: SttLink | None = (
            SttLink(stt, user.language, on_event=self._on_stt_event, on_status=self._on_link_status)
            if mode == "voice"
            else None
        )

        # What the browser's VAD and the provider have told us about the user's speech.
        self._user_speaking = False
        self._awaiting_final = False  # commit sent, transcript not back yet
        self._speech_end: float | None = None
        self._commit_sent: float | None = None
        self._prefix = ""  # words of a speculative turn dropped because the user kept talking
        self._prefix_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._closed = False

    # ------------------------------------------------------------ Conversation port

    async def start(self) -> None:
        await self._state.transition(VoiceState.LISTENING)
        # While the STT socket opens (seconds), the history loads and the TTS socket warms up.
        side_work = [
            asyncio.create_task(self._restore_history(), name="history-restore"),
            asyncio.create_task(self._warm_up_tts(), name="tts-warm-up"),
        ]
        try:
            if self._link is not None:
                await self._link.start()
            await asyncio.wait(side_work)
        finally:
            for task in side_work:
                task.cancel()
            await asyncio.wait(side_work)

    async def on_audio(self, chunk: bytes) -> None:
        if self._link is not None and self._state.state not in _DEAF_STATES:
            self._link.push_audio(chunk)

    async def on_speech_started(self) -> None:
        self._user_speaking = True
        self._speech_end = self._commit_sent = None
        self._awaiting_final = False
        self._cancel_prefix_flush()
        await self._interrupt("barge_in")

    async def on_speech_ended(self) -> None:
        if self._link is None:
            return
        self._user_speaking = False
        self._speech_end = perf_counter()
        self._awaiting_final = True
        self._link.commit()
        self._commit_sent = perf_counter()
        if self._prefix:  # the continuation is over; give its transcript a moment to arrive
            self._schedule_prefix_flush()

    async def on_text(self, text: str) -> None:
        if text := text.strip():
            self._prefix = ""
            self._cancel_prefix_flush()
            await self._start_turn(text, speculative=False, from_voice=False)

    async def on_cancel(self, generation_id: int) -> None:
        await self._turns.cancel("client_cancel", generation_id)

    async def on_playback_started(self, generation_id: int) -> None:
        turn = self._turns.current
        if turn and turn.generation_id == generation_id and turn.playback_at is None:
            turn.playback_at = perf_counter()
            turn.timer.mark("playback_started")

    async def on_playback_finished(self, generation_id: int) -> None:
        turn = self._turns.current
        if turn and turn.generation_id == generation_id:
            turn.playback_finished = True  # may arrive before the reply is fully generated
            if turn.generated:
                await self._complete(turn)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_prefix_flush()
        if self._link is not None:
            await self._link.close()
        await self._turns.close()
        if self._background:
            _, pending = await asyncio.wait(self._background, timeout=HOOK_GRACE_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending)
        await self._state.transition(VoiceState.IDLE)

    # ------------------------------------------------------------ history

    async def _restore_history(self) -> None:
        """Bring back what an earlier connection of this conversation already said."""
        if self._load_history is None or not self._user.save_history:
            return
        try:
            async with asyncio.timeout(HISTORY_LOAD_TIMEOUT_S):
                stored = await self._load_history()
        except Exception:  # history is a convenience; the call goes on without it
            log.warning("history not restored", exc_info=True)
            return
        self._history.restore(stored)
        self._turns.resume_numbering(sum(1 for m in stored if m.role == "user"))

    async def _warm_up_tts(self) -> None:
        """Transports with a persistent socket open it now, so the first reply skips the
        handshake. Optional: the plain HTTP transport has nothing to warm."""
        warm_up = getattr(self._tts, "warm_up", None)
        if self._mode != "voice" or warm_up is None:
            return
        try:
            async with asyncio.timeout(TTS_WARM_UP_TIMEOUT_S):
                await warm_up()
        except Exception:  # the first reply connects on demand instead
            log.warning("tts warm-up failed", exc_info=True)

    # ------------------------------------------------------------ speech-to-text

    async def _on_stt_event(self, event: STTEvent) -> None:
        match event:
            case SpeechStarted():
                if not self._awaiting_final:  # else it is the utterance we just committed
                    await self._interrupt("barge_in")
            case TranscriptEvent(is_final=True):
                self._awaiting_final = False
                if event.text.strip():
                    await self._on_final(event.text.strip(), event.language)

    async def _on_link_status(self, status: LinkStatus, error: ProviderError | None) -> None:
        self._awaiting_final = False
        match status:
            case LinkStatus.RECONNECTING:
                await self._send_error("stt_error", _provider_message(error), retryable=True)
                if self._state.state is VoiceState.LISTENING:
                    await self._state.transition(VoiceState.RECONNECTING)
            case LinkStatus.RESTORED:
                if self._state.state is VoiceState.RECONNECTING:
                    await self._state.transition(VoiceState.LISTENING)
            case LinkStatus.FAILED:
                await self._send_error("stt_error", _provider_message(error), retryable=False)
                await self._turns.cancel("stt_error")
                await self._state.transition(VoiceState.ERROR)

    async def _on_final(self, text: str, language: str | None) -> None:
        arrived = perf_counter()
        current = self._turns.current
        if current is not None and current.speculative:
            # The user paused and went on: one utterance, not two.
            self._prefix = current.user_text
            await self._turns.cancel("merged", current.generation_id)
        text = self._merge_prefix(text)
        if self._user_speaking:  # talking again already: wait for the rest
            self._stash(text)
            return
        await self._start_turn(
            text,
            speculative=self._speech_end is None,  # no client VAD verdict: hold the audio briefly
            from_voice=True,
            language=language,
            arrived=arrived,
        )

    # ------------------------------------------------------------ speculative-turn bookkeeping

    async def _interrupt(self, reason: str) -> None:
        """The user is speaking over a turn. An unconfirmed one made no sound yet: drop it
        silently and keep its words; a confirmed one is a barge-in."""
        turn = self._turns.current
        if turn is None:
            return
        if turn.speculative:
            self._prefix = turn.user_text
            reason = "speech_resumed"
        await self._turns.cancel(reason, turn.generation_id)

    def _merge_prefix(self, text: str) -> str:
        if self._prefix:
            text = f"{self._prefix} {text}"
            self._prefix = ""
        self._cancel_prefix_flush()
        return text

    def _stash(self, text: str) -> None:
        self._prefix = text
        self._schedule_prefix_flush()

    def _schedule_prefix_flush(self) -> None:
        self._cancel_prefix_flush()
        self._prefix_task = asyncio.create_task(self._flush_prefix(), name="prefix-flush")

    def _cancel_prefix_flush(self) -> None:
        task = self._prefix_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            self._prefix_task = None

    async def _flush_prefix(self) -> None:
        try:
            await asyncio.sleep(RESUMED_HOLD_S)
            text, self._prefix = self._prefix, ""
            if text:
                await self._start_turn(text, speculative=False, from_voice=True)
        finally:
            if self._prefix_task is asyncio.current_task():
                self._prefix_task = None

    # ------------------------------------------------------------ turn lifecycle

    async def _start_turn(
        self,
        text: str,
        *,
        speculative: bool,
        from_voice: bool,
        language: str | None = None,
        arrived: float | None = None,
    ) -> None:
        if self._closed:
            return
        await self._turns.cancel("superseded")  # a new utterance replaces any reply in flight
        if self._state.state is not VoiceState.LISTENING:
            log.info("input ignored", extra={"state": self._state.state})
            return
        turn = await self._turns.begin(
            text,
            speculative=speculative,
            grace_s=self._settings.endpoint_grace_ms / 1000,
            language=language,
            from_voice=from_voice,
        )
        if from_voice:
            turn.timer.mark("stt_final", arrived)
            if self._speech_end is not None:
                turn.timer.mark("speech_end", self._speech_end)
            if self._commit_sent is not None:
                turn.timer.mark("commit_sent", self._commit_sent)
        self._speech_end = self._commit_sent = None
        log.info("turn started", extra={"turn_id": turn.turn_id, "chars": len(text)})

    async def _on_turn_cancelled(self, turn: Turn, reason: str) -> None:
        """Called once the cancelled turn's tasks are gone."""
        if turn.speculative:
            return  # never confirmed: the browser saw nothing, the caller keeps the words
        await self._end_interrupted(turn, reason)

    async def _complete(self, turn: Turn) -> None:
        async with self._turns.ending(turn) as owned:
            if not owned:
                return  # cancelled first
            await self._finalize(turn, turn.reply.strip(), interrupted=False)
            await self._enter_listening()

    async def _fail(self, turn: Turn, code: str, message: str, retryable: bool) -> None:
        await turn.confirmed.wait()  # a speculative turn reports nothing the user did not hear
        async with self._turns.ending(turn) as owned:
            if not owned:
                return
            await self._send_error(code, message, retryable, turn)
            await self._end_interrupted(turn, "error")

    async def _end_interrupted(self, turn: Turn, reason: str) -> None:
        """INTERRUPTED -> `turn.cancelled` -> history + metrics -> LISTENING. The browser is only
        told if it was told the turn had started."""
        heard, cut_short = self._heard(turn)
        announced = not self._closed and self._state.state in _TURN_STATES
        if announced:
            await self._state.transition(VoiceState.INTERRUPTED)
            await self._send(
                TurnCancelled(
                    turn_id=turn.turn_id,
                    generation_id=turn.generation_id,
                    reason=reason,
                    heard_text=heard,
                )
            )
        await self._finalize(turn, heard, cut_short)
        if announced:
            await self._enter_listening()

    async def _enter_listening(self) -> None:
        if self._state.state in (VoiceState.INTERRUPTED, *_TURN_STATES):
            await self._state.transition(VoiceState.LISTENING)

    def _heard(self, turn: Turn) -> tuple[str, bool]:
        """What the user actually received, and whether the reply was cut short. For
        voice, the browser's playback start plus the audio duration we sent tells which
        sentences finished playing; the rest never reached their ears."""
        full = turn.reply.strip()
        if self._mode == "text":
            return full, True
        started = turn.playback_at or turn.audio_sent_at
        if started is None:
            return "", True
        elapsed = perf_counter() - started
        if turn.generated and elapsed >= turn.audio_bytes / self._bytes_per_second:
            return full, False
        return " ".join(text for text, end in turn.sentence_ends if end <= elapsed), True

    async def _finalize(self, turn: Turn, heard: str, interrupted: bool) -> None:
        """Commit the exchange to history, hand it to the hooks, emit metrics. Runs once per
        turn and never waits for the hooks."""
        self._history.add_user(turn.user_text)
        self._history.add_assistant(heard, interrupted=interrupted)
        if self._on_message is not None:
            self._spawn("on_message", self._persist(turn.user_text, heard, interrupted))
        if self._after_turn is not None:
            self._spawn("after_turn", self._after_turn(self._user, self._history.messages))
        metrics = turn.timer.metrics(turn.turn_id, turn.generation_id)
        latency_stats.record(metrics)
        log.info(
            "turn finished",
            extra={
                "turn_id": turn.turn_id,
                "interrupted": interrupted,
                "metrics": metrics.model_dump(exclude_none=True),
            },
        )
        await self._send(metrics)

    async def _persist(self, user_text: str, heard: str, interrupted: bool) -> None:
        assert self._on_message is not None
        await self._on_message("user", user_text, False)
        if heard:
            await self._on_message("assistant", heard, interrupted)

    def _spawn(self, name: str, work: Awaitable[None]) -> None:
        task = asyncio.create_task(self._guarded(name, work), name=f"hook-{name}")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    @staticmethod
    async def _guarded(name: str, work: Awaitable[None]) -> None:
        try:
            await work
        except Exception:
            log.exception("post-turn hook failed", extra={"hook": name})

    # ------------------------------------------------------------ the turn itself

    async def _run_turn(self, turn: Turn) -> None:
        outbox = TurnOutbox()
        if turn.from_voice:
            outbox.event(TranscriptFinal(text=turn.user_text, language=turn.language))
        outbox.state(VoiceState.PROCESSING)
        failure: tuple[str, str, bool] | None = None
        try:
            await self._pipeline.run(turn, self._reply(turn, outbox), outbox)
        except* ProviderError as group:
            exc = group.exceptions[0]
            assert isinstance(exc, ProviderError)
            log.warning(
                "provider failed", extra={"provider": exc.provider, "turn_id": turn.turn_id}
            )
            failure = ("provider_error", _provider_message(exc), exc.retryable)
        except* Exception:
            log.exception("turn crashed", extra={"turn_id": turn.turn_id})
            failure = ("internal_error", "Something went wrong", False)
        if failure is not None:
            await self._fail(turn, *failure)
            return
        await self._finish_generation(turn)

    async def _finish_generation(self, turn: Turn) -> None:
        """All text produced and all audio sent. Done now if there is nothing left to play,
        else when the browser reports it finished playing."""
        await self._output.event(turn, TurnCompleted(text=turn.reply.strip()))
        turn.generated = True
        if not turn.audio_started or turn.playback_finished:
            await self._complete(turn)

    async def _reply(self, turn: Turn, outbox: TurnOutbox) -> AsyncGenerator[str, None]:
        """The reply's text deltas. Everything before the first token - memories, context -
        happens here, then the browser is told generation started."""
        memories = await self._recall(turn.user_text)
        safety_note = self._crisis.check(turn.user_text)
        if safety_note:
            log.info("crisis language detected", extra={"turn_id": turn.turn_id})
        messages = self._context.build(
            self._user, self._history, memories, turn.user_text, safety_note
        )
        outbox.state(VoiceState.GENERATING)
        outbox.event(LlmStarted())
        turn.timer.mark("llm_request")
        async with closing(self._llm_text(turn, messages)) as deltas:
            async for delta in deltas:
                yield delta

    async def _recall(self, query: str) -> list[Memory]:
        if self._memory is None or not self._user.memory_enabled:
            return []
        try:
            async with asyncio.timeout(MEMORY_BUDGET_S):
                return await self._memory.relevant(self._user.user_id, query)
        except TimeoutError:
            log.info("memory recall skipped: over budget")
        except Exception:
            log.exception("memory recall failed")
        return []

    async def _llm_text(self, turn: Turn, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """Yield the reply's text deltas, running the tool loop (bounded per turn) between
        LLM rounds. Tool calls and results stay in `messages`; they are never spoken."""
        tools_used = 0
        limit = self._settings.max_tool_calls_per_turn
        while True:
            tools = self._tools if tools_used < limit else None
            text: list[str] = []
            calls: list[ToolCall] = []
            async with closing(
                self._llm.stream(messages, tools.specs() if tools else [])
            ) as chunks:
                async for chunk in chunks:
                    if chunk.text:
                        turn.timer.mark("llm_first_token")
                        text.append(chunk.text)
                        yield chunk.text
                    calls.extend(chunk.tool_calls)
            if tools is None or not calls:
                return
            calls = calls[: limit - tools_used]
            tools_used += len(calls)
            messages.append(ChatMessage("assistant", "".join(text), tool_calls=tuple(calls)))
            for call in calls:
                result = await self._run_tool(tools, call)
                messages.append(ChatMessage("tool", result, tool_call_id=call.id))

    async def _run_tool(self, tools: ToolRunner, call: ToolCall) -> str:
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_S):
                return await tools.run(call, self._user)
        except TimeoutError:
            log.warning("tool timed out", extra={"tool": call.name})
            return f"Error: {call.name} timed out."

    # ------------------------------------------------------------ output

    async def _announce(self, state: VoiceState) -> None:
        if not self._closed:
            await self._sink.send_event(StateChanged(state=state))

    async def _send(self, event: ServerEvent) -> None:
        if not self._closed:
            await self._sink.send_event(event)

    async def _send_error(
        self, code: str, message: str, retryable: bool, turn: Turn | None = None
    ) -> None:
        await self._send(
            PipelineError(
                turn_id=turn.turn_id if turn else None,
                generation_id=turn.generation_id if turn else None,
                code=code,
                message=message,
                retryable=retryable,
            )
        )


_DEAF_STATES = frozenset({VoiceState.IDLE, VoiceState.RECONNECTING, VoiceState.ERROR})
_TURN_STATES = frozenset({VoiceState.PROCESSING, VoiceState.GENERATING, VoiceState.SPEAKING})


def _provider_message(error: ProviderError | None) -> str:
    return f"{error.provider} failed" if error else "speech service failed"
