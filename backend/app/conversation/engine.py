"""VoiceConversation: the orchestrator behind one live conversation.

Flow per turn: final transcript (or typed text) -> memories + crisis check -> context ->
LLM stream -> sentence segmenter -> TTS (next sentence synthesises while the current one
streams) -> audio frames tagged with a generation id. A barge-in cancels the turn task,
fences the generation so no further frame can leave, and tells the browser.

Concurrency rules this module relies on:
  * A turn is one owned asyncio task; it never ends a turn on its own except through
    `_complete` / `_fail`, which take `self._lock`.
  * Every send for a turn is guarded by `self._live_generation`. Interrupting clears it
    *before* anything is awaited, so no frame of the old generation is sent afterwards.
  * `self._lock` serialises everything that starts or ends a turn (barge-in, new
    transcript, completion, failure, STT reconnect).
Spec: docs/context/voice-pipeline.md
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Literal

from app.config import Settings
from app.conversation.context import ContextBuilder
from app.conversation.history import ConversationHistory
from app.conversation.ports import (
    Conversation,
    EventSink,
    Memory,
    MemoryRetriever,
    ToolRunner,
    UserContext,
)
from app.providers.base import (
    ChatMessage,
    LanguageModel,
    ProviderError,
    SpeechStarted,
    SpeechToText,
    SpeechToTextStream,
    STTEvent,
    TextToSpeech,
    ToolCall,
    TranscriptEvent,
)
from app.safety.crisis import CrisisDetector
from app.voice.latency import TurnTimer, latency_stats
from app.voice.segmenter import SentenceSegmenter
from app.voice.state import VoiceState, VoiceStateMachine
from app.websocket.protocol import (
    AssistantAudioStart,
    AssistantDone,
    AssistantText,
    ErrorEvent,
    Interrupted,
    ServerEvent,
    StateChanged,
    TranscriptFinal,
    TranscriptPartial,
)

log = logging.getLogger(__name__)

MEMORY_BUDGET_S = 0.15  # recall is a bonus; never let it hold up the reply
MAX_SYNTHESES_IN_FLIGHT = 2  # the sentence being played and the one being prefetched
SYNTHESIS_BUFFER_CHUNKS = 8
TOOL_TIMEOUT_S = 8.0
HOOK_GRACE_S = 2.0  # how long close() lets persistence hooks finish before cancelling them

MessageHook = Callable[[str, str, bool], Awaitable[None]]
AfterTurnHook = Callable[[UserContext, list[ChatMessage]], Awaitable[None]]
_AudioChunks = asyncio.Queue[bytes | None]


@dataclass(slots=True)
class _Turn:
    """Everything one user utterance and Oryn's reply to it need to share."""

    turn_id: int
    generation_id: int
    user_text: str
    timer: TurnTimer
    task: asyncio.Task[None] = field(init=False)
    reply: str = ""
    done: bool = False  # all text generated and all audio sent
    # voice pipeline: sentences in speaking order, each with its audio buffer
    playlist: asyncio.Queue[tuple[str, _AudioChunks] | None] = field(default_factory=asyncio.Queue)
    slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_SYNTHESES_IN_FLIGHT)
    )
    audio_started: bool = False
    audio_bytes: int = 0
    audio_sent_at: float | None = None
    playback_at: float | None = None  # when the browser said audio became audible
    sentence_ends: list[tuple[str, float]] = field(default_factory=list)  # (text, audio secs)


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
    ) -> None:
        self._user = user
        self._mode = mode
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._sink = sink
        self._settings = settings
        self._memory = memory
        self._tools = tools
        self._after_turn = after_turn
        self._on_message = on_message

        self._state = VoiceStateMachine(self._announce)
        self._history = ConversationHistory(settings.context_max_turns)
        self._context = ContextBuilder(settings.context_max_turns)
        self._crisis = CrisisDetector()
        fmt = tts.output_format
        self._bytes_per_second = fmt.sample_rate * fmt.channels * 2

        self._lock = asyncio.Lock()
        self._stream: SpeechToTextStream | None = None
        self._reader: asyncio.Task[None] | None = None
        self._turn: _Turn | None = None
        self._live_generation: int | None = None
        self._turn_count = 0
        self._generation_count = 0
        self._speech_end: float | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._closed = False

    # ------------------------------------------------------------ Conversation port

    async def start(self) -> None:
        await self._state.transition(VoiceState.LISTENING)
        if self._mode == "voice":
            await self._open_stream()

    async def on_audio(self, chunk: bytes) -> None:
        if self._mode != "voice" or self._state.state in _DEAF_STATES:
            return
        try:
            stream = self._stream or await self._open_stream()
            await stream.send_audio(chunk)
        except ProviderError as exc:
            await self._on_stt_error(self._stream, exc)

    async def on_audio_end(self) -> None:
        self._speech_end = perf_counter()
        if (stream := self._stream) is None:
            return
        try:
            await stream.finish()
        except ProviderError as exc:
            await self._on_stt_error(stream, exc)

    async def on_text(self, text: str) -> None:
        if text.strip():
            await self._begin_turn(text.strip(), language=None, announce=False)

    async def on_interrupt(self) -> None:
        async with self._lock:
            await self._stop_locked(notify=True)

    async def on_playback_started(self, generation_id: int) -> None:
        turn = self._turn
        if turn and turn.generation_id == generation_id and turn.playback_at is None:
            turn.playback_at = perf_counter()
            turn.timer.mark("playback_started")

    async def on_playback_finished(self, generation_id: int) -> None:
        # Before assistant.done the browser's queue may just be momentarily empty.
        turn = self._turn
        if turn and turn.generation_id == generation_id and turn.done:
            await self._complete(turn)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.wait({self._reader})
        async with self._lock:
            await self._stop_locked(notify=False)
        if self._background:
            _, pending = await asyncio.wait(self._background, timeout=HOOK_GRACE_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending)
        if self._stream is not None:
            await self._close_stream(self._stream)
            self._stream = None
        await self._state.transition(VoiceState.IDLE)

    # ------------------------------------------------------------ speech-to-text

    async def _open_stream(self) -> SpeechToTextStream:
        stream = await self._stt.open_stream(self._user.language)
        self._stream = stream
        self._reader = asyncio.create_task(self._read_stt(stream), name="stt-reader")
        return stream

    async def _read_stt(self, stream: SpeechToTextStream) -> None:
        try:
            async for event in stream.events():
                await self._on_stt_event(event)
        except ProviderError as exc:
            await self._on_stt_error(stream, exc)
            return
        if self._stream is stream:  # ended after finish(); the next audio opens a fresh one
            self._stream = None
        await self._close_stream(stream)

    async def _on_stt_event(self, event: STTEvent) -> None:
        match event:
            case SpeechStarted():
                if self._state.state in (VoiceState.GENERATING, VoiceState.SPEAKING):
                    async with self._lock:
                        await self._stop_locked(notify=True)
            case TranscriptEvent(is_final=False) if event.text.strip():
                await self._sink.send_event(TranscriptPartial(text=event.text))
            case TranscriptEvent(is_final=True) if event.text.strip():
                await self._begin_turn(event.text.strip(), language=event.language, announce=True)

    async def _on_stt_error(self, stream: SpeechToTextStream | None, exc: ProviderError) -> None:
        """Retryable: reopen the stream once. Otherwise (or if that fails) the session is
        ERROR. Holds the lock during the reopen so no turn starts on a half-open stream."""
        async with self._lock:
            if self._closed or stream is not self._stream:
                return  # already handled by the reader or the audio path
            self._stream = None
            log.warning("stt failed", extra={"provider": exc.provider, "retryable": exc.retryable})
            await self._send_error("stt_error", f"{exc.provider} failed", exc.retryable)
            if stream is not None:
                await self._close_stream(stream)
            if not exc.retryable:
                await self._enter_error_locked()
                return
            reconnecting = self._state.state is VoiceState.LISTENING
            if reconnecting:
                await self._state.transition(VoiceState.RECONNECTING)
            try:
                await self._open_stream()
            except ProviderError as retry_exc:
                await self._send_error("stt_error", f"{retry_exc.provider} failed", False)
                await self._enter_error_locked()
                return
            if reconnecting:
                await self._state.transition(VoiceState.LISTENING)

    async def _enter_error_locked(self) -> None:
        await self._stop_locked(notify=True)
        await self._state.transition(VoiceState.ERROR)

    @staticmethod
    async def _close_stream(stream: SpeechToTextStream) -> None:
        try:
            await stream.close()
        except Exception:
            log.exception("closing stt stream failed")

    # ------------------------------------------------------------ turn lifecycle

    async def _begin_turn(self, text: str, *, language: str | None, announce: bool) -> None:
        async with self._lock:
            await self._stop_locked(notify=True)  # a new utterance replaces any reply in flight
            if self._state.state is not VoiceState.LISTENING:
                log.info("input ignored", extra={"state": self._state.state})
                return
            self._turn_count += 1
            self._generation_count += 1
            timer = TurnTimer()
            timer.mark("stt_final")
            if self._speech_end is not None:
                timer.mark("speech_end", self._speech_end)
                self._speech_end = None
            turn = _Turn(self._turn_count, self._generation_count, text, timer)
            if announce:
                await self._sink.send_event(
                    TranscriptFinal(turn_id=turn.turn_id, text=text, language=language)
                )
            self._turn = turn
            self._live_generation = turn.generation_id
            await self._state.transition(VoiceState.PROCESSING)
            turn.task = asyncio.create_task(self._run_turn(turn), name=f"turn-{turn.turn_id}")
            log.info("turn started", extra={"turn_id": turn.turn_id, "chars": len(text)})

    async def _stop_locked(self, *, notify: bool) -> None:
        """Barge-in / teardown. Idempotent: with no turn in flight it does nothing."""
        turn = self._turn
        if turn is None:
            return
        self._turn = None
        self._live_generation = None  # fence: nothing for this generation is sent after here
        turn.timer.mark("interrupt")
        turn.task.cancel()
        try:
            if notify:
                await self._state.transition(VoiceState.INTERRUPTED)
                await self._sink.send_event(Interrupted(generation_id=turn.generation_id))
        finally:  # a dead sink must not leave the cancelled task un-awaited
            await asyncio.wait({turn.task})
        turn.timer.mark("stop_complete")
        await self._finalize(turn, *self._heard(turn))
        if notify:
            await self._state.transition(VoiceState.LISTENING)

    async def _complete(self, turn: _Turn) -> None:
        async with self._lock:
            if self._turn is not turn:
                return  # interrupted first
            self._turn = None
            self._live_generation = None
            await self._finalize(turn, turn.reply.strip(), interrupted=False)
            await self._state.transition(VoiceState.LISTENING)

    async def _fail(self, turn: _Turn, code: str, message: str, retryable: bool) -> None:
        async with self._lock:
            if self._turn is not turn:
                return
            self._turn = None
            self._live_generation = None
            await self._send_error(code, message, retryable)
            await self._finalize(turn, *self._heard(turn))
            # The turn is aborted, not the session: INTERRUPTED -> LISTENING is the way back.
            await self._state.transition(VoiceState.INTERRUPTED)
            await self._state.transition(VoiceState.LISTENING)

    def _heard(self, turn: _Turn) -> tuple[str, bool]:
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
        if turn.done and elapsed >= turn.audio_bytes / self._bytes_per_second:
            return full, False
        return " ".join(text for text, end in turn.sentence_ends if end <= elapsed), True

    async def _finalize(self, turn: _Turn, heard: str, interrupted: bool) -> None:
        """Commit the exchange to history, hand it to the hooks, emit metrics. Runs once
        per turn, under the lock, and never waits for the hooks."""
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
                "metrics": metrics.model_dump(),
            },
        )
        if not self._closed:
            await self._sink.send_event(metrics)

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

    async def _run_turn(self, turn: _Turn) -> None:
        failure: tuple[str, str, bool] | None = None
        try:
            await self._respond(turn)
        except* ProviderError as group:
            exc = group.exceptions[0]
            assert isinstance(exc, ProviderError)  # one task group, so never nested
            log.warning(
                "provider failed", extra={"provider": exc.provider, "turn_id": turn.turn_id}
            )
            failure = ("provider_error", f"{exc.provider} failed", exc.retryable)
        except* Exception:
            log.exception("turn crashed", extra={"turn_id": turn.turn_id})
            failure = ("internal_error", "Something went wrong", False)
        if failure is not None:
            await self._fail(turn, *failure)

    async def _respond(self, turn: _Turn) -> None:
        memories = await self._recall(turn.user_text)
        safety_note = self._crisis.check(turn.user_text)
        if safety_note:
            log.info("crisis language detected", extra={"turn_id": turn.turn_id})
        messages = self._context.build(
            self._user, self._history, memories, turn.user_text, safety_note
        )
        await self._enter(turn, VoiceState.GENERATING)
        turn.timer.mark("llm_request")

        speaking = self._mode == "voice"
        segmenter = SentenceSegmenter()
        async with asyncio.TaskGroup() as tasks:
            if speaking:
                tasks.create_task(self._play(turn))
            async for delta in self._llm_text(turn, messages):
                turn.reply += delta
                await self._send(turn, AssistantText(generation_id=turn.generation_id, delta=delta))
                for sentence in segmenter.feed(delta) if speaking else ():
                    self._say(tasks, turn, sentence)
            if speaking:
                if rest := segmenter.flush():
                    self._say(tasks, turn, rest)
                turn.playlist.put_nowait(None)
        # the task group has exited: all audio is out
        await self._send(
            turn, AssistantDone(generation_id=turn.generation_id, text=turn.reply.strip())
        )
        turn.done = True
        if not turn.audio_started:  # text mode, or nothing speakable: no playback to wait for
            await self._complete(turn)

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

    async def _llm_text(self, turn: _Turn, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """Yield the reply's text deltas, running the tool loop (bounded per turn) between
        LLM rounds. Tool calls and results stay in `messages`; they are never spoken."""
        tools_used = 0
        limit = self._settings.max_tool_calls_per_turn
        while True:
            tools = self._tools if tools_used < limit else None
            text: list[str] = []
            calls: list[ToolCall] = []
            async for chunk in self._llm.stream(messages, tools.specs() if tools else []):
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

    # ------------------------------------------------------------ text-to-speech pipeline

    def _say(self, tasks: asyncio.TaskGroup, turn: _Turn, sentence: str) -> None:
        """Start synthesising `sentence` now; `_play` emits it in order."""
        chunks: _AudioChunks = asyncio.Queue(maxsize=SYNTHESIS_BUFFER_CHUNKS)
        turn.playlist.put_nowait((sentence, chunks))
        tasks.create_task(self._synthesize(turn, sentence, chunks))

    async def _synthesize(self, turn: _Turn, sentence: str, chunks: _AudioChunks) -> None:
        await turn.slots.acquire()  # released by _play once this sentence has been sent
        turn.timer.mark("tts_request")
        voice = self._settings.gnani_tts_voice or None
        async for pcm in self._tts.synthesize(sentence, language=self._user.language, voice=voice):
            turn.timer.mark("tts_first_byte")
            await chunks.put(pcm)
        await chunks.put(None)

    async def _play(self, turn: _Turn) -> None:
        """Send audio strictly in sentence order, whatever order syntheses finish in."""
        while (item := await turn.playlist.get()) is not None:
            sentence, chunks = item
            while (pcm := await chunks.get()) is not None:
                if not pcm:
                    continue
                if not turn.audio_started:
                    turn.audio_started = True
                    start = AssistantAudioStart(
                        generation_id=turn.generation_id,
                        sample_rate=self._tts.output_format.sample_rate,
                    )
                    await self._send(turn, start)
                    await self._enter(turn, VoiceState.SPEAKING)
                await self._send_audio(turn, pcm)
                turn.audio_bytes += len(pcm)
                turn.audio_sent_at = turn.audio_sent_at or perf_counter()
                turn.timer.mark("first_audio_sent")
            turn.sentence_ends.append((sentence, turn.audio_bytes / self._bytes_per_second))
            turn.slots.release()

    # ------------------------------------------------------------ guarded output

    async def _announce(self, state: VoiceState) -> None:
        if not self._closed:
            await self._sink.send_event(StateChanged(state=state))

    async def _enter(self, turn: _Turn, state: VoiceState) -> None:
        if self._live_generation == turn.generation_id:
            await self._state.transition(state)

    async def _send(self, turn: _Turn, event: ServerEvent) -> None:
        if self._live_generation == turn.generation_id:
            await self._sink.send_event(event)

    async def _send_audio(self, turn: _Turn, pcm: bytes) -> None:
        if self._live_generation == turn.generation_id:
            await self._sink.send_audio(turn.generation_id, pcm)

    async def _send_error(self, code: str, message: str, retryable: bool) -> None:
        await self._sink.send_event(ErrorEvent(code=code, message=message, retryable=retryable))


_DEAF_STATES = frozenset({VoiceState.IDLE, VoiceState.RECONNECTING, VoiceState.ERROR})
