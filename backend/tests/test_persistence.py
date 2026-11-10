import json

import httpx
import pytest
from pydantic import SecretStr

from app.auth.profiles import ProfileRepository
from app.config import Settings
from app.conversation.ports import UserContext
from app.persistence.conversations import (
    InMemoryConversationRepository,
    PostgrestConversationRepository,
    StoredMessage,
)
from app.persistence.postgrest import PersistenceError, PostgrestClient

USER = "7b0c3b5e-6a52-4f0e-9a63-0d8f8f1b2c11"
OTHER = "0d4f3a52-1111-4222-8333-944455556666"
CONVERSATION = "c0ffee00-1111-4222-8333-944455556666"
KEY = "service-role-key"


def _client(handler: httpx.MockTransport) -> PostgrestClient:
    settings = Settings(
        supabase_url="https://proj.supabase.co/",
        supabase_service_role_key=SecretStr(KEY),
    )
    return PostgrestClient(settings, httpx.AsyncClient(transport=handler))


class _Recorder:
    def __init__(self, status: int = 200, body: object = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status, self._body = status, body if body is not None else []

    @property
    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self._status, json=self._body)

        return httpx.MockTransport(handle)


async def test_profile_request_targets_verified_user_with_service_key() -> None:
    recorder = _Recorder(body=[{"preferred_name": "Asha"}])
    await ProfileRepository(_client(recorder.transport)).load(USER)

    request = recorder.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/rest/v1/profiles"
    assert request.url.params["id"] == f"eq.{USER}"
    assert request.headers["apikey"] == KEY
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "save_history" in request.url.params["select"]  # privacy opt-out must be loaded


async def test_profile_row_maps_to_user_context() -> None:
    row = {
        "preferred_name": "Asha",
        "primary_language": "hi-IN",
        "extra_languages": ["en-IN"],
        "style": "calm",
        "response_length": "detailed",
        "listen_first": False,
        "memory_enabled": True,
        "save_history": False,
        "timezone": "Asia/Dubai",
        "interests": ["cricket"],
        "voice": "Nalini",
    }
    context = await ProfileRepository(_client(_Recorder(body=[row]).transport)).load(USER)
    assert context == UserContext(
        user_id=USER,
        preferred_name="Asha",
        language="hi-IN",
        extra_languages=("en-IN",),
        style="calm",
        response_length="detailed",
        listen_first=False,
        memory_enabled=True,
        save_history=False,
        timezone="Asia/Dubai",
        interests=("cricket",),
        voice="Nalini",
    )


async def test_auto_primary_language_becomes_auto_detect_and_blank_voice_is_none() -> None:
    row = {"primary_language": "auto", "voice": None}
    context = await ProfileRepository(_client(_Recorder(body=[row]).transport)).load(USER)
    assert context.language == "auto"
    assert context.voice is None


async def test_profile_request_selects_the_voice_column() -> None:
    recorder = _Recorder()
    await ProfileRepository(_client(recorder.transport)).load(USER)
    assert "voice" in recorder.requests[0].url.params["select"].split(",")


async def test_missing_profile_row_yields_defaults() -> None:
    context = await ProfileRepository(_client(_Recorder(body=[]).transport)).load(USER)
    assert context == UserContext(user_id=USER)


async def test_database_failure_degrades_to_defaults() -> None:
    context = await ProfileRepository(_client(_Recorder(status=500).transport)).load(USER)
    assert context == UserContext(user_id=USER)


async def test_unconfigured_client_refuses_to_send_requests() -> None:
    client = PostgrestClient(Settings())
    with pytest.raises(PersistenceError):
        await client.select("profiles", {})


async def test_create_conversation_inserts_owner_and_returns_id() -> None:
    recorder = _Recorder(status=201, body=[{"id": CONVERSATION}])
    repo = PostgrestConversationRepository(_client(recorder.transport))

    assert await repo.create(USER, "voice") == CONVERSATION

    request = recorder.requests[0]
    assert request.method == "POST" and request.url.path == "/rest/v1/conversations"
    assert json.loads(request.content) == {"user_id": USER, "mode": "voice"}
    assert request.headers["prefer"] == "return=representation"


async def test_owns_filters_by_conversation_and_user() -> None:
    recorder = _Recorder(body=[{"id": CONVERSATION}])
    repo = PostgrestConversationRepository(_client(recorder.transport))

    assert await repo.owns(CONVERSATION, USER) is True

    params = recorder.requests[0].url.params
    assert params["id"] == f"eq.{CONVERSATION}" and params["user_id"] == f"eq.{USER}"


async def test_owns_rejects_non_uuid_without_a_request() -> None:
    recorder = _Recorder()
    repo = PostgrestConversationRepository(_client(recorder.transport))
    assert await repo.owns("x&user_id=neq.0", USER) is False
    assert recorder.requests == []


async def test_add_message_posts_scoped_row() -> None:
    recorder = _Recorder(status=201, body=[{"id": 1}])
    repo = PostgrestConversationRepository(_client(recorder.transport))

    await repo.add_message(CONVERSATION, USER, "assistant", "hello", True)

    assert json.loads(recorder.requests[0].content) == {
        "conversation_id": CONVERSATION,
        "user_id": USER,
        "role": "assistant",
        "content": "hello",
        "interrupted": True,
    }


async def test_recent_filters_by_user_and_returns_oldest_first() -> None:
    newest_first = [
        {"role": "assistant", "content": "b", "interrupted": False},
        {"role": "user", "content": "a", "interrupted": False},
    ]
    recorder = _Recorder(body=newest_first)
    repo = PostgrestConversationRepository(_client(recorder.transport))

    messages = await repo.recent(CONVERSATION, USER, limit=12)

    params = recorder.requests[0].url.params
    assert params["user_id"] == f"eq.{USER}" and params["limit"] == "12"
    assert [m.content for m in messages] == ["a", "b"]


async def test_in_memory_repository_enforces_ownership() -> None:
    repo = InMemoryConversationRepository()
    conversation_id = await repo.create(USER, "text")
    await repo.add_message(conversation_id, USER, "user", "hi", False)

    assert await repo.owns(conversation_id, USER)
    assert not await repo.owns(conversation_id, OTHER)
    assert await repo.recent(conversation_id, OTHER, 10) == []
    assert await repo.recent(conversation_id, USER, 10) == [StoredMessage("user", "hi")]
    with pytest.raises(KeyError):
        await repo.add_message(conversation_id, OTHER, "user", "x", False)
