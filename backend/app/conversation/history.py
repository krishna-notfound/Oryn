"""Rolling record of the conversation as the model should remember it."""

from __future__ import annotations

from collections.abc import Iterable

from app.persistence.conversations import StoredMessage
from app.providers.base import ChatMessage


class ConversationHistory:
    """The last `max_turns` user/assistant exchanges, verbatim. Older messages are not lost:
    they move to an overflow list the engine folds into the rolling summary."""

    def __init__(self, max_turns: int) -> None:
        self._max_messages = 2 * max_turns
        self._messages: list[ChatMessage] = []
        self._overflow: list[ChatMessage] = []

    @property
    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def add_user(self, text: str) -> None:
        self._append(ChatMessage("user", text))

    def add_assistant(self, text: str) -> None:
        """`text` is only what the user actually heard. Nothing marks a cut-off reply here;
        the context builder tells the model about it for the next turn only."""
        if text:  # an empty reply (cut off before a word was heard) is not a message
            self._append(ChatMessage("assistant", text))

    def restore(self, stored: Iterable[StoredMessage]) -> None:
        """Reload messages saved by an earlier connection of the same conversation."""
        for message in stored:
            if message.role == "user":
                self.add_user(message.content)
            else:
                self.add_assistant(message.content)

    def take_overflow(self) -> list[ChatMessage]:
        """Messages that no longer fit, oldest first; each is handed out once."""
        overflow, self._overflow = self._overflow, []
        return overflow

    def _append(self, message: ChatMessage) -> None:
        self._messages.append(message)
        excess = len(self._messages) - self._max_messages
        if excess <= 0:
            return
        while excess < len(self._messages) and self._messages[excess].role != "user":
            excess += 1  # an exchange is never split: its orphaned reply goes with it
        self._overflow.extend(self._messages[:excess])
        del self._messages[:excess]
