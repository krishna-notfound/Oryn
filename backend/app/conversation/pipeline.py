"""The staged, bounded pipeline that turns one reply's text into audio on the wire.

    LLM reader --deltas--> segmenter --segments--> TTS worker --pcm--> audio sender
         \\--> outbox (text.delta / text.segment / state events) --> text sender

One `TaskGroup` per turn; every hop is a bounded queue, so a slow client blocks the audio
sender, which fills the pcm buffer, which pauses the TTS read, which fills the segment queue,
which stalls the segmenter, which stops the LLM read. Nothing grows without bound.

Text and state events travel on a separate path (`TurnOutbox`) that never blocks the stages
above: consecutive text deltas coalesce while the socket is slow, so a stalled text write can
never delay audio. Everything client-visible waits on `turn.confirmed` (the speculative-turn
audio gate) before it is sent; synthesis may run ahead of the gate, sending may not.
Generators are closed with `aclosing`, `CancelledError` is never swallowed.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from app.conversation.streams import closing
from app.conversation.turns import Turn
from app.providers.base import TextToSpeech
from app.voice.segmenter import SentenceSegmenter
from app.voice.state import VoiceState
from app.websocket.protocol import (
    AudioCompleted,
    AudioSegment,
    AudioSegmentDone,
    AudioStarted,
    ServerEvent,
    TextDelta,
    TextSegment,
)

SEGMENT_QUEUE_SIZE = 4
DELTA_QUEUE_SIZE = 16
PCM_BUFFER_SECONDS = 2.0


class TurnOutput(Protocol):
    """Where a turn's output leaves. Implementations drop anything from a fenced-off
    generation; the pipeline itself only decides *order* and *pacing*."""

    async def event(self, turn: Turn, event: ServerEvent) -> None: ...

    async def state(self, turn: Turn, state: VoiceState) -> None: ...

    async def audio(self, turn: Turn, seq: int, pcm: bytes) -> None: ...


class TurnOutbox:
    """Ordered client-visible events and state changes of one turn. Producers never wait;
    `drain` sends them once the turn is confirmed, merging text deltas that piled up."""

    def __init__(self) -> None:
        self._items: deque[ServerEvent | VoiceState] = deque()
        self._wake = asyncio.Event()
        self._closed = False

    def event(self, event: ServerEvent) -> None:
        self._items.append(event)
        self._wake.set()

    def state(self, state: VoiceState) -> None:
        self._items.append(state)
        self._wake.set()

    def delta(self, text: str) -> None:
        last = self._items[-1] if self._items else None
        if isinstance(last, TextDelta):
            self._items[-1] = last.model_copy(update={"delta": last.delta + text})
        else:
            self._items.append(TextDelta(delta=text))
        self._wake.set()

    def close(self) -> None:
        """No more items; `drain` returns after sending what is left."""
        self._closed = True
        self._wake.set()

    async def drain(self, turn: Turn, output: TurnOutput) -> None:
        await turn.confirmed.wait()
        while True:
            while self._items:
                match item := self._items.popleft():
                    case VoiceState():
                        await output.state(turn, item)
                        if item is VoiceState.GENERATING:
                            turn.generating.set()
                    case _:
                        await output.event(turn, item)
            if self._closed:
                return
            self._wake.clear()
            await self._wake.wait()


class ByteBoundedQueue[T]:
    """FIFO whose `put` waits while more than `max_bytes` are queued. An item bigger than the
    whole budget is still accepted into an empty queue, so a large chunk can never deadlock."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._items: deque[tuple[T, int]] = deque()
        self._queued = 0
        self._changed = asyncio.Condition()

    async def put(self, item: T, size: int = 0) -> None:
        async with self._changed:
            await self._changed.wait_for(
                lambda: self._queued == 0 or self._queued + size <= self._max_bytes
            )
            self._items.append((item, size))
            self._queued += size
            self._changed.notify_all()

    async def get(self) -> T:
        async with self._changed:
            await self._changed.wait_for(lambda: bool(self._items))
            item, size = self._items.popleft()
            self._queued -= size
            self._changed.notify_all()
            return item


@dataclass(frozen=True, slots=True)
class _Segment:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class _Audio:
    pcm: bytes
    segment: _Segment


@dataclass(frozen=True, slots=True)
class _SegmentEnd:
    segment: _Segment


_PcmItem = _Audio | _SegmentEnd | None


class TurnPipeline:
    def __init__(
        self,
        output: TurnOutput,
        tts: TextToSpeech | None,
        *,
        language: str,
        voice: str | None,
    ) -> None:
        """`tts=None` is a text-only turn: the reply is streamed as text and nothing is spoken.
        `language` and `voice` are the defaults; a turn that resolved its own wins."""
        self._output = output
        self._tts = tts
        self._language = language
        self._voice = voice

    async def run(self, turn: Turn, source: AsyncGenerator[str, None], outbox: TurnOutbox) -> None:
        """Pipe the reply text from `source` to the client. Returns when everything has been
        sent; raises the first stage failure after cancelling the others."""
        loop = asyncio.get_running_loop()
        async with asyncio.TaskGroup() as group:
            group.create_task(outbox.drain(turn, self._output), name="text-sender")
            if self._tts is None:
                group.create_task(self._read_llm(turn, source, None, outbox), name="llm-reader")
                return
            fmt = self._tts.output_format
            deltas = asyncio.Queue[str | None](DELTA_QUEUE_SIZE)
            segments = asyncio.Queue[_Segment | None](SEGMENT_QUEUE_SIZE)
            pcm = ByteBoundedQueue[_PcmItem](int(PCM_BUFFER_SECONDS * fmt.sample_rate * 2))
            group.create_task(self._read_llm(turn, source, deltas, outbox), name="llm-reader")
            group.create_task(self._segment(turn, deltas, segments, outbox, loop), name="segmenter")
            group.create_task(self._synthesize(turn, self._tts, segments, pcm), name="tts-worker")
            group.create_task(self._send_audio(turn, fmt.sample_rate, pcm), name="audio-sender")

    # ------------------------------------------------------------ stages

    @staticmethod
    async def _read_llm(
        turn: Turn,
        source: AsyncGenerator[str, None],
        deltas: asyncio.Queue[str | None] | None,
        outbox: TurnOutbox,
    ) -> None:
        async with closing(source) as stream:
            async for delta in stream:
                turn.reply += delta
                outbox.delta(delta)
                if deltas is not None:
                    await deltas.put(delta)
        outbox.close()
        if deltas is not None:
            await deltas.put(None)

    @staticmethod
    async def _segment(
        turn: Turn,
        deltas: asyncio.Queue[str | None],
        segments: asyncio.Queue[_Segment | None],
        outbox: TurnOutbox,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        segmenter = SentenceSegmenter()
        index = 0

        async def emit(texts: list[str]) -> None:
            nonlocal index
            for text in texts:
                turn.timer.mark("first_segment")
                outbox.event(TextSegment(index=index, text=text))
                await segments.put(_Segment(index, text))
                index += 1

        while True:
            deadline = segmenter.deadline
            try:
                async with asyncio.timeout(None if deadline is None else deadline - loop.time()):
                    delta = await deltas.get()
            except TimeoutError:  # the first segment waited long enough; speak what we have
                await emit(segmenter.poll(loop.time()))
                continue
            if delta is None:
                break
            await emit(segmenter.feed(delta, loop.time()))
        await emit([tail] if (tail := segmenter.flush()) else [])
        await segments.put(None)

    async def _synthesize(
        self,
        turn: Turn,
        tts: TextToSpeech,
        segments: asyncio.Queue[_Segment | None],
        pcm: ByteBoundedQueue[_PcmItem],
    ) -> None:
        while (segment := await segments.get()) is not None:
            turn.timer.mark("tts_request")
            stream = tts.synthesize(
                segment.text,
                language=turn.language or self._language,
                voice=turn.voice or self._voice,
            )
            async with closing(stream) as audio:
                async for chunk in audio:
                    turn.timer.mark("tts_first_byte")
                    if chunk:
                        await pcm.put(_Audio(chunk, segment), len(chunk))
            await pcm.put(_SegmentEnd(segment))
        await pcm.put(None)

    async def _send_audio(
        self, turn: Turn, sample_rate: int, pcm: ByteBoundedQueue[_PcmItem]
    ) -> None:
        bytes_per_second = sample_rate * 2
        seq = 0
        open_segment: _Segment | None = None  # announced, not yet closed
        segment_bytes = 0
        while (item := await pcm.get()) is not None:
            match item:
                case _SegmentEnd(segment=segment):
                    turn.sentence_ends.append((segment.text, turn.audio_bytes / bytes_per_second))
                    if open_segment == segment:  # a segment that made no sound is never announced
                        done = AudioSegmentDone(index=segment.index, samples=segment_bytes // 2)
                        await self._output.event(turn, done)
                        open_segment = None
                case _Audio(pcm=chunk, segment=segment):
                    if not turn.audio_started:
                        await turn.generating.wait()  # confirmed, and GENERATING went out first
                        turn.audio_started = True
                        await self._output.event(turn, AudioStarted(sample_rate=sample_rate))
                        await self._output.state(turn, VoiceState.SPEAKING)
                    if open_segment != segment:
                        open_segment, segment_bytes = segment, 0
                        announce = AudioSegment(
                            index=segment.index, text=segment.text, first_seq=seq
                        )
                        await self._output.event(turn, announce)
                    await self._output.audio(turn, seq, chunk)
                    seq += 1
                    segment_bytes += len(chunk)
                    turn.audio_bytes += len(chunk)
                    turn.audio_sent_at = turn.audio_sent_at or perf_counter()
                    turn.timer.mark("first_audio_sent")
        if turn.audio_started:
            await self._output.event(turn, AudioCompleted())
