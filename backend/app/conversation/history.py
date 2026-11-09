"""Rolling record of the conversation as the model should remember it."""

from __future__ import annotations

from collections import deque

from app.conversation.prompts import INTERRUPTED_MARKER
from app.providers.base import ChatMessage


class ConversationHistory:
    """The last `max_turns` user/assistant exchanges (oldest dropped first)."""

    def __init__(self, max_turns: int) -> None:
        self._messages: deque[ChatMessage] = deque(maxlen=2 * max_turns)

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def add_user(self, text: str) -> None:
        self._messages.append(ChatMessage("user", text))

    def add_assistant(self, text: str, *, interrupted: bool = False) -> None:
        """`text` must be only what the user actually heard. If they cut Oryn off, a
        marker tells the model so, instead of letting it assume the user heard everything."""
        content = f"{text} {INTERRUPTED_MARKER}".strip() if interrupted else text
        self._messages.append(ChatMessage("assistant", content))
