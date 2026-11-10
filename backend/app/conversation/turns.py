"""Turn lifecycle: who owns the current turn, and how it ends.

`TurnManager` is the single owner of `turn_id`, `generation_id`, the active `Turn` and
cancellation. Its one hard rule: `live_generation` (the fence every outbound send checks) is
cleared *synchronously* when a turn is cancelled or ended - there is no `await` between
deciding to end a turn and clearing the fence - so a late send of the old generation is
impossible however the tasks interleave. There is no lock around provider calls; the only
serialisation is `begin()` waiting for an in-flight cancel/end to settle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from app.voice.latency import TurnTimer

log = logging.getLogger(__name__)

CANCEL_GRACE_S = 2.0  # how long cancel() waits for a turn's tasks to unwind before moving on


@dataclass(eq=False, slots=True)
class Turn:
    """Everything one user utterance and Oryn's reply to it share."""

    turn_id: int
    generation_id: int
    user_text: str
    timer: TurnTimer
    speculative: bool  # started on a final transcript; audio is held until confirmed
    language: str | None = None  # the turn's language: what TTS speaks and the browser is told
    voice: str | None = None  # Timbre voice for that language
    from_voice: bool = False  # spoken input: the transcript is announced to the browser
    confirmed: asyncio.Event = field(default_factory=asyncio.Event)  # the audio gate
    generating: asyncio.Event = field(default_factory=asyncio.Event)  # GENERATING was announced
    task: asyncio.Task[None] | None = None
    grace: asyncio.TimerHandle | None = None
    reply: str = ""
    generated: bool = False  # all text produced and all audio sent
    playback_finished: bool = False  # the browser reported it played everything
    audio_started: bool = False
    audio_bytes: int = 0
    audio_sent_at: float | None = None
    playback_at: float | None = None  # when the browser said audio became audible
    sentence_ends: list[tuple[str, float]] = field(default_factory=list)  # (text, audio secs)


Runner = Callable[[Turn], Coroutine[Any, Any, None]]
CancelHandler = Callable[[Turn, str], Awaitable[None]]


class TurnManager:
    def __init__(self, run: Runner, on_cancelled: CancelHandler) -> None:
        self._run = run
        self._on_cancelled = on_cancelled
        self._turn: Turn | None = None
        self._live_generation: int | None = None
        self._turn_count = 0
        self._generation_count = 0
        self._begin_lock = asyncio.Lock()
        self._settled = asyncio.Event()  # no cancel/end is mid-flight
        self._settled.set()

    @property
    def live_generation(self) -> int | None:
        return self._live_generation

    @property
    def current(self) -> Turn | None:
        return self._turn

    def resume_numbering(self, turns_so_far: int) -> None:
        """Continue ids after a reconnect so they keep increasing within a conversation."""
        self._turn_count = max(self._turn_count, turns_so_far)
        self._generation_count = max(self._generation_count, turns_so_far)

    async def begin(
        self,
        text: str,
        *,
        speculative: bool,
        grace_s: float = 0.0,
        language: str | None = None,
        voice: str | None = None,
        from_voice: bool = False,
    ) -> Turn:
        """Start a turn, replacing any in flight. A speculative turn confirms itself after
        `grace_s` unless `confirm()` or `cancel()` comes first."""
        async with self._begin_lock:  # starts are serialised; the lock covers no provider I/O
            await self.cancel("superseded")
            await self._settled.wait()
            self._turn_count += 1
            self._generation_count += 1
            turn = Turn(
                self._turn_count,
                self._generation_count,
                text,
                TurnTimer(),
                speculative=speculative,
                language=language,
                voice=voice,
                from_voice=from_voice,
            )
            turn.timer.mark("turn_started")
            if speculative:
                turn.grace = asyncio.get_running_loop().call_later(grace_s, self.confirm, turn)
            else:
                turn.confirmed.set()
            self._turn = turn
            self._live_generation = turn.generation_id
            turn.task = asyncio.create_task(self._run(turn), name=f"turn-{turn.turn_id}")
            return turn

    def confirm(self, turn: Turn) -> None:
        """Open the audio gate. Idempotent; a no-op for a turn that already ended."""
        self._disarm(turn)
        if self._turn is turn:
            turn.speculative = False
            turn.confirmed.set()

    async def cancel(self, reason: str, generation_id: int | None = None) -> bool:
        """End the current turn. False if there is none or `generation_id` is stale (the
        browser answering generation N after N+1 began must not kill N+1)."""
        turn = self._turn
        if turn is None or (generation_id is not None and generation_id != turn.generation_id):
            return False
        # ---- the fence: everything up to the first await is synchronous
        self._turn = None
        self._live_generation = None
        self._settled.clear()
        self._disarm(turn)
        turn.timer.mark("cancel_requested")
        # ----
        try:
            await self._reap(turn)
            turn.timer.mark("cancel_completed")
            await self._on_cancelled(turn, reason)
        finally:
            self._settled.set()
        return True

    @contextlib.asynccontextmanager
    async def ending(self, turn: Turn) -> AsyncIterator[bool]:
        """Claim the normal end of `turn` (completed or failed). Yields False when something
        else (a cancel) already ended it. `begin()` waits for the block to finish, so the next
        turn never starts on a half-finished one."""
        if self._turn is not turn:
            yield False
            return
        self._turn = None
        self._live_generation = None
        self._settled.clear()
        self._disarm(turn)
        try:
            yield True
        finally:
            self._settled.set()

    async def close(self, reason: str = "shutdown") -> None:
        await self.cancel(reason)
        await self._settled.wait()

    # ------------------------------------------------------------ internals

    @staticmethod
    def _disarm(turn: Turn) -> None:
        if turn.grace is not None:
            turn.grace.cancel()
            turn.grace = None

    @staticmethod
    async def _reap(turn: Turn) -> None:
        """Cancel the turn's task and wait for its whole TaskGroup to unwind."""
        task = turn.task
        if task is None or task is asyncio.current_task():
            return  # a turn ending itself (never the case today) must not await its own task
        task.cancel()
        # asyncio.wait neither raises the task's CancelledError nor swallows ours.
        _, pending = await asyncio.wait({task}, timeout=CANCEL_GRACE_S)
        if pending:
            log.error("turn did not stop in time", extra={"turn_id": turn.turn_id})
        elif not task.cancelled() and (exc := task.exception()) is not None:
            log.error("turn task failed while stopping", exc_info=exc)
