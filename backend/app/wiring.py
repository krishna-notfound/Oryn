"""Live wiring: the long-lived services, and one VoiceConversation per WebSocket session."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.conversation.engine import HistoryLoader, MessageHook, VoiceConversation
from app.conversation.ports import Conversation, EventSink, UserContext
from app.memory.extractor import MemoryExtractor
from app.memory.store import SQLiteMemoryStore
from app.persistence.conversations import ConversationRepository, Role, StoredMessage
from app.providers.registry import build_providers
from app.tools.builtin import build_default_tools
from app.tools.registry import ToolRegistry
from app.websocket.protocol import SessionStart

TOOL_HTTP_TIMEOUT_S = 6.0

# Languages both Gnani Prisma (STT) and Gnani Timbre (TTS) support - see docs/context/providers.md.
SUPPORTED_LANGUAGES = frozenset(
    {"hi-IN", "en-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"}
)


class OrynRuntime:
    """Owns everything that lives as long as the process - providers, the memory store,
    the tool registry and the HTTP client the tools share - and builds a conversation for
    each authenticated session. Create once at startup; `aclose()` on shutdown."""

    def __init__(self, settings: Settings, conversations: ConversationRepository) -> None:
        self._settings = settings
        self._conversations = conversations
        self.providers = build_providers(settings)
        self.memory = SQLiteMemoryStore(settings.memory_db_path, self.providers.embedder)
        self._http = httpx.AsyncClient(timeout=TOOL_HTTP_TIMEOUT_S)
        self.tools = ToolRegistry(build_default_tools(self.memory, self._http))
        self._extract_memories = MemoryExtractor(
            self.providers.llm, self.memory, self.providers.embedder
        )

    def create_conversation(
        self, user: UserContext, start: SessionStart, sink: EventSink, conversation_id: str
    ) -> Conversation:
        """The `ConversationFactory` the WebSocket layer calls after authentication."""
        if start.language in SUPPORTED_LANGUAGES:  # the call screen may switch language per call
            user = dataclasses.replace(user, language=start.language)
        p = self.providers
        return VoiceConversation(
            user=user,
            mode=start.mode,
            stt=p.stt,
            llm=p.llm,
            tts=p.tts,
            sink=sink,
            settings=self._settings,
            memory=self.memory,
            tools=self.tools,
            after_turn=self._extract_memories,
            on_message=self._history_writer(user, conversation_id),
            load_history=self._history_reader(user, conversation_id),
        )

    def _history_reader(self, user: UserContext, conversation_id: str) -> HistoryLoader:
        """What an earlier connection of this conversation said (empty for a new one), so a
        reconnect does not reset Oryn's short-term memory."""
        limit = 2 * self._settings.context_max_turns

        async def load() -> Sequence[StoredMessage]:
            return await self._conversations.recent(conversation_id, user.user_id, limit)

        return load

    def _history_writer(self, user: UserContext, conversation_id: str) -> MessageHook | None:
        """Persist the transcript only for users who keep history on."""
        if not user.save_history:
            return None
        repository = self._conversations

        async def write(role: str, text: str, interrupted: bool) -> None:
            speaker: Role = "assistant" if role == "assistant" else "user"
            await repository.add_message(conversation_id, user.user_id, speaker, text, interrupted)

        return write

    async def aclose(self) -> None:
        await self._http.aclose()
        await self.providers.aclose()
        self.memory.close()
