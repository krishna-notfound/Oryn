"""A running summary of the conversation, kept off the reply path.

`ConversationHistory` keeps the last few turns verbatim; what falls out of it is folded here
into a short summary the context builder adds to the system prompt. The fold is a background
LLM call owned by this object: `fold()` returns at once, at most one call runs at a time,
every call is time-boxed, and a failure only logs - the messages stay queued (bounded) and are
retried with the next fold. The reply path only ever reads `text`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from app.conversation.streams import closing
from app.providers.base import ChatMessage, LanguageModel

log = logging.getLogger(__name__)

SUMMARY_WORDS = 150
SUMMARY_TIMEOUT_S = 20.0
SUMMARY_MAX_TOKENS = 300
MAX_PENDING_MESSAGES = 40  # older queued messages are dropped rather than grown without bound
MAX_MESSAGE_CHARS = 600  # one long story reply must not blow up the summarisation prompt

_INSTRUCTIONS = (
    "You keep a running summary of a spoken conversation between a user and Oryn, a voice "
    "companion. Merge the new exchanges into the summary. Keep names, facts, plans, feelings, "
    "open questions, and anything Oryn was in the middle of telling. Plain prose, no lists, "
    f"at most {SUMMARY_WORDS} words, in the language mix the user speaks. Reply with the "
    "updated summary only."
)


class RollingSummary:
    def __init__(
        self,
        llm: LanguageModel,
        *,
        timeout_s: float = SUMMARY_TIMEOUT_S,
        max_pending: int = MAX_PENDING_MESSAGES,
    ) -> None:
        self._llm = llm
        self._timeout_s = timeout_s
        self._max_pending = max_pending
        self._text = ""
        self._pending: list[ChatMessage] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def text(self) -> str:
        return self._text

    def fold(self, messages: Sequence[ChatMessage]) -> None:
        """Queue messages that left the verbatim history and make sure a worker is running."""
        if not messages:
            return
        self._pending.extend(messages)
        del self._pending[: -self._max_pending]
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work(), name="rolling-summary")

    async def settle(self) -> None:
        """Wait for the running fold to finish (tests, shutdown)."""
        if self._task is not None:
            await asyncio.wait({self._task})

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait({self._task})

    async def _work(self) -> None:
        while self._pending:
            batch = list(self._pending)
            try:
                async with asyncio.timeout(self._timeout_s):
                    updated = await self._summarise(batch)
            except Exception:  # the summary is a bonus; the call goes on without it
                log.warning("summary update failed", exc_info=True)
                return
            del self._pending[: len(batch)]
            if updated:
                self._text = updated

    async def _summarise(self, batch: Sequence[ChatMessage]) -> str:
        prompt = [
            ChatMessage("system", _INSTRUCTIONS),
            ChatMessage("user", self._request(batch)),
        ]
        parts: list[str] = []
        async with closing(self._llm.stream(prompt, max_tokens=SUMMARY_MAX_TOKENS)) as chunks:
            async for chunk in chunks:
                parts.append(chunk.text)
        return " ".join("".join(parts).split()[:SUMMARY_WORDS])

    def _request(self, batch: Sequence[ChatMessage]) -> str:
        lines = [
            f"{'User' if m.role == 'user' else 'Oryn'}: {m.content[:MAX_MESSAGE_CHARS]}"
            for m in batch
        ]
        return f"Summary so far: {self._text or '(none yet)'}\n\nNew exchanges:\n" + "\n".join(
            lines
        )
