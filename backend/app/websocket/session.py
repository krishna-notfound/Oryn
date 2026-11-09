"""One WebSocket connection = one `VoiceSession`.

Lifecycle: accept -> `session.start` (with a deadline) -> verify JWT -> load profile ->
resolve conversation -> `session.ready` -> route frames until the socket ends.
The session knows only the `Conversation` port; it never sees providers.
Logs carry ids and durations only - never transcripts, audio or tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, NoReturn

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.types import Message

from app.auth.profiles import ProfileRepository
from app.auth.supabase import Authenticator, AuthError
from app.config import Settings
from app.conversation.ports import Conversation, EventSink, UserContext
from app.persistence.conversations import ConversationRepository
from app.persistence.postgrest import PersistenceError
from app.providers.base import ProviderError
from app.voice.latency import latency_stats
from app.websocket.protocol import (
    MAX_AUDIO_FRAME_BYTES,
    ClientTurnMetrics,
    Heartbeat,
    InputSpeechEnded,
    InputSpeechStarted,
    PipelineError,
    PlaybackFinished,
    PlaybackStarted,
    ServerEvent,
    ServerHeartbeat,
    SessionEnd,
    SessionReady,
    SessionStart,
    TurnCancel,
    UserText,
    encode_event,
    pack_audio_frame,
    parse_client_message,
)
from app.websocket.ratelimit import TokenBucket

log = logging.getLogger(__name__)

INPUT_SAMPLE_RATE = 16_000
MAX_STRIKES = 3
MAX_TEXT_FRAME_BYTES = 65_536  # a JWT plus headroom; user.text is capped far lower
# Nominal capture is ~50 frames/s; allow double that with a one-second burst.
FRAME_BURST = 100
FRAMES_PER_SECOND = 100.0

ConversationFactory = Callable[[UserContext, SessionStart, EventSink, str], Conversation]


class CloseCode(IntEnum):
    NORMAL = 1000
    INTERNAL_ERROR = 1011
    BAD_MESSAGE = 4400
    UNAUTHENTICATED = 4401
    HANDSHAKE_TIMEOUT = 4408
    SUPERSEDED = 4409
    RATE_LIMITED = 4429


class WebSocketEventSink(EventSink):
    """Serialises writes to the socket and silently drops them once it is gone."""

    def __init__(self, websocket: WebSocket, session_id: str) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._lock = asyncio.Lock()
        self._seq = 0
        self._closed = False

    async def send_event(self, event: ServerEvent) -> None:
        await self._send(event)

    async def send_audio(self, generation_id: int, seq: int, pcm: bytes) -> None:
        await self._send(pack_audio_frame(generation_id, seq, pcm))

    def close(self) -> None:
        self._closed = True

    async def _send(self, payload: ServerEvent | bytes) -> None:
        if self._closed:
            return
        async with self._lock:
            if self._closed:
                return
            try:
                if isinstance(payload, bytes):
                    await self._websocket.send_bytes(payload)
                else:
                    self._seq += 1  # taken under the lock, so numbers follow wire order
                    text = encode_event(
                        payload,
                        session_id=self._session_id,
                        seq=self._seq,
                        ts=int(time.time() * 1000),
                    )
                    await self._websocket.send_text(text)
            except (RuntimeError, WebSocketDisconnect):
                self._closed = True


class _CloseSession(Exception):
    def __init__(self, code: CloseCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SessionServices:
    settings: Settings
    authenticator: Authenticator
    profiles: ProfileRepository
    conversations: ConversationRepository
    conversation_factory: ConversationFactory
    registry: SessionRegistry


class VoiceSession:
    def __init__(self, websocket: WebSocket, services: SessionServices) -> None:
        self.session_id = uuid.uuid4().hex
        self._ws = websocket
        self._services = services
        self._sink = WebSocketEventSink(websocket, self.session_id)
        self._bucket = TokenBucket(FRAME_BURST, FRAMES_PER_SECOND)
        self._conversation: Conversation | None = None
        self._user_id: str | None = None
        self._task: asyncio.Task[Any] | None = None
        self._evict_code: CloseCode | None = None
        self._closing = False
        self._strikes = 0

    async def run(self) -> None:
        started = time.monotonic()
        self._task = asyncio.current_task()
        code = CloseCode.NORMAL
        try:
            await self._ws.accept()
            conversation = await self._handshake()
            async with asyncio.timeout(self._services.settings.session_max_duration_s):
                await self._route_frames(conversation)
        except _CloseSession as stop:
            code = stop.code
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            await self._sink.send_event(
                PipelineError(code="session_limit", message="Session time limit reached.")
            )
        except asyncio.CancelledError:
            if self._evict_code is None:
                raise
            code = self._evict_code
            if self._task is not None:
                self._task.uncancel()
        except Exception:
            log.exception("session crashed", extra={"session_id": self.session_id})
            await self._sink.send_event(
                PipelineError(code="internal", message="Something went wrong.", retryable=True)
            )
            code = CloseCode.INTERNAL_ERROR
        finally:
            await self._shutdown(code, started)

    def evict(self, code: CloseCode) -> None:
        """Called by the registry (from another session's task) to end this session."""
        if self._closing or self._task is None:
            return
        self._evict_code = code
        self._task.cancel()

    # ------------------------------------------------------------ handshake

    async def _handshake(self) -> Conversation:
        svc = self._services
        try:
            async with asyncio.timeout(svc.settings.session_handshake_timeout_s):
                first = await self._receive()
        except TimeoutError:
            await self._fail(
                CloseCode.HANDSHAKE_TIMEOUT, "handshake_timeout", "Handshake timed out."
            )

        start = self._parse_start(first.get("text"))
        try:
            user = await svc.authenticator.verify(start.token)
        except AuthError:
            await self._fail(CloseCode.UNAUTHENTICATED, "unauthenticated", "Authentication failed.")

        self._user_id = user.user_id
        context = await svc.profiles.load(user.user_id)
        svc.registry.register(user.user_id, self)
        conversation_id = await self._resolve_conversation(user.user_id, start)
        conversation = svc.conversation_factory(context, start, self._sink, conversation_id)
        self._conversation = conversation
        try:
            await conversation.start()
        except ProviderError as exc:
            log.warning("conversation failed to start", extra={"provider": exc.provider})
            await self._fail(
                CloseCode.INTERNAL_ERROR,
                "provider_unavailable",
                "Voice service is unavailable, please retry.",
            )
        # Ready only now: opening the STT stream takes seconds, and a client that streams the mic
        # on `session.ready` would otherwise queue a burst that trips the frame rate limit.
        await self._sink.send_event(
            SessionReady(
                conversation_id=conversation_id,
                input_sample_rate=INPUT_SAMPLE_RATE,
                output_sample_rate=svc.settings.gnani_tts_sample_rate,
            )
        )
        log.info("session started", extra={"session_id": self.session_id, "user_id": user.user_id})
        return conversation

    async def _fail(self, code: CloseCode, error_code: str, message: str) -> NoReturn:
        await self._sink.send_event(PipelineError(code=error_code, message=message))
        raise _CloseSession(code)

    def _parse_start(self, text: str | None) -> SessionStart:
        if text is None or len(text) > MAX_TEXT_FRAME_BYTES:
            raise _CloseSession(CloseCode.BAD_MESSAGE)
        try:
            message = parse_client_message(text)
        except ValidationError:
            raise _CloseSession(CloseCode.BAD_MESSAGE) from None
        if not isinstance(message, SessionStart):
            raise _CloseSession(CloseCode.BAD_MESSAGE)
        return message

    async def _resolve_conversation(self, user_id: str, start: SessionStart) -> str:
        """Reuse the client's conversation only if it is verifiably theirs."""
        repo = self._services.conversations
        try:
            if start.conversation_id and await repo.owns(start.conversation_id, user_id):
                return start.conversation_id
            return await repo.create(user_id, start.mode)
        except PersistenceError as exc:
            # History is optional; a live conversation is not. Run without persistence.
            log.warning("conversation not persisted", extra={"error": str(exc)})
            return str(uuid.uuid4())

    # ------------------------------------------------------------ routing

    async def _receive(self) -> Message:
        frame = await self._ws.receive()
        if frame["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(frame.get("code", CloseCode.NORMAL))
        return frame

    async def _route_frames(self, conversation: Conversation) -> None:
        while True:
            frame = await self._receive()
            if not self._bucket.allow():
                await self._fail(CloseCode.RATE_LIMITED, "rate_limited", "Too many frames.")
            if (audio := frame.get("bytes")) is not None:
                await self._on_audio(conversation, audio)
            elif (text := frame.get("text")) is not None:
                await self._on_text(conversation, text)

    async def _on_audio(self, conversation: Conversation, chunk: bytes) -> None:
        if not 0 < len(chunk) <= MAX_AUDIO_FRAME_BYTES:
            await self._strike("Invalid audio frame size.")
            return
        await conversation.on_audio(chunk)

    async def _on_text(self, conversation: Conversation, raw: str) -> None:
        try:
            if len(raw) > MAX_TEXT_FRAME_BYTES:
                raise ValueError("frame too large")
            message = parse_client_message(raw)
        except (ValidationError, ValueError):
            await self._strike("Malformed message.")
            return
        match message:
            case UserText(text=text):
                await conversation.on_text(text)
            case InputSpeechStarted():
                await conversation.on_speech_started()
            case InputSpeechEnded():
                await conversation.on_speech_ended()
            case TurnCancel(generation_id=generation_id):
                await conversation.on_cancel(generation_id)
            case PlaybackStarted(generation_id=generation_id):
                await conversation.on_playback_started(generation_id)
            case PlaybackFinished(generation_id=generation_id):
                await conversation.on_playback_finished(generation_id)
            case ClientTurnMetrics():
                self._record_client_metrics(message)
            case Heartbeat():
                await self._sink.send_event(ServerHeartbeat())
            case SessionEnd():
                raise _CloseSession(CloseCode.NORMAL)
            case SessionStart():
                await self._strike("Session already started.")

    def _record_client_metrics(self, message: ClientTurnMetrics) -> None:
        latency_stats.record_perceived(message.perceived_ms)
        log.info(
            "client turn metrics",
            extra={
                "session_id": self.session_id,
                "turn_id": message.turn_id,
                "perceived_ms": message.perceived_ms,
                "playback_lead_ms": message.playback_lead_ms,
            },
        )

    async def _strike(self, message: str) -> None:
        self._strikes += 1
        await self._sink.send_event(PipelineError(code="bad_message", message=message))
        if self._strikes >= MAX_STRIKES:
            raise _CloseSession(CloseCode.BAD_MESSAGE)

    # ------------------------------------------------------------ teardown

    async def _shutdown(self, code: CloseCode, started: float) -> None:
        self._closing = True
        self._sink.close()
        if self._user_id is not None:
            self._services.registry.release(self._user_id, self)
        with contextlib.suppress(Exception):  # socket may already be gone
            await self._ws.close(code)
        if self._conversation is not None:
            try:
                await self._conversation.close()
            except Exception:
                log.exception("conversation close failed", extra={"session_id": self.session_id})
        log.info(
            "session closed",
            extra={
                "session_id": self.session_id,
                "user_id": self._user_id,
                "close_code": int(code),
                "duration_s": round(time.monotonic() - started, 1),
            },
        )


class SessionRegistry:
    """At most one live session per user; a newer one supersedes the older."""

    def __init__(self) -> None:
        self._by_user: dict[str, VoiceSession] = {}

    def register(self, user_id: str, session: VoiceSession) -> None:
        previous = self._by_user.get(user_id)
        self._by_user[user_id] = session
        if previous is not None:
            previous.evict(CloseCode.SUPERSEDED)

    def release(self, user_id: str, session: VoiceSession) -> None:
        if self._by_user.get(user_id) is session:
            del self._by_user[user_id]

    def __len__(self) -> int:
        return len(self._by_user)
