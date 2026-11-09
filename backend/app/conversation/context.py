"""Assembles the exact message list sent to the LLM for one turn."""

from __future__ import annotations

from collections.abc import Sequence

from app.conversation.history import ConversationHistory
from app.conversation.ports import Memory, UserContext
from app.conversation.prompts import build_system_prompt
from app.providers.base import ChatMessage

DEFAULT_MAX_CHARS = 8_000


class ContextBuilder:
    """System prompt (persona + memories + safety note) followed by as much recent
    history as fits in `max_turns` exchanges and `max_chars` characters."""

    def __init__(self, max_turns: int, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self._max_messages = 2 * max_turns
        self._max_chars = max_chars

    def build(
        self,
        user: UserContext,
        history: ConversationHistory,
        memories: Sequence[Memory],
        user_text: str,
        safety_note: str | None = None,
    ) -> list[ChatMessage]:
        system = ChatMessage("system", self._system_prompt(user, memories, safety_note))
        current = ChatMessage("user", user_text)
        budget = self._max_chars - len(system.content) - len(current.content)
        return [system, *self._recent(history.messages, budget), current]

    @staticmethod
    def _system_prompt(
        user: UserContext, memories: Sequence[Memory], safety_note: str | None
    ) -> str:
        parts = [build_system_prompt(user)]
        if memories:
            facts = "\n".join(f"- {m.content}" for m in memories)
            parts.append(
                "Things you remember about the user. Use them only when they help; "
                f"do not recite them:\n{facts}"
            )
        if safety_note:
            parts.append(safety_note)
        return "\n\n".join(parts)

    def _recent(self, messages: list[ChatMessage], budget: int) -> list[ChatMessage]:
        kept: list[ChatMessage] = []
        for message in reversed(messages[-self._max_messages :]):
            budget -= len(message.content)
            if budget < 0:
                break
            kept.append(message)
        kept.reverse()
        while kept and kept[0].role != "user":  # never start mid-exchange
            kept.pop(0)
        return kept
