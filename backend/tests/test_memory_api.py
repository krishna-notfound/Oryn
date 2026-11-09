from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memory.api import build_memory_router
from app.memory.store import SQLiteMemoryStore
from app.providers.fakes import FakeEmbedder

TOKENS = {"token-alice": "alice", "token-bob": "bob"}


async def fake_authenticate(token: str) -> str:
    try:
        return TOKENS[token]
    except KeyError:
        raise PermissionError("bad token") from None


def auth(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer token-{user}"}


@pytest.fixture
def store() -> Iterator[SQLiteMemoryStore]:
    s = SQLiteMemoryStore(":memory:", FakeEmbedder())
    yield s
    s.close()


@pytest.fixture
def client(store: SQLiteMemoryStore) -> TestClient:
    app = FastAPI()
    app.include_router(build_memory_router(store, fake_authenticate))
    return TestClient(app)


async def seed(store: SQLiteMemoryStore, user: str, text: str) -> str:
    return (await store.add(user, text, "preference")).id


async def test_lists_only_the_callers_memories(
    client: TestClient, store: SQLiteMemoryStore
) -> None:
    await seed(store, "alice", "likes chai")
    await seed(store, "bob", "likes coffee")
    body = client.get("/memories", headers=auth("alice")).json()
    assert [m["content"] for m in body["memories"]] == ["likes chai"]
    assert set(body["memories"][0]) == {"id", "content", "category", "confidence"}


async def test_patch_updates_content(client: TestClient, store: SQLiteMemoryStore) -> None:
    memory_id = await seed(store, "alice", "likes chai")
    response = client.patch(
        f"/memories/{memory_id}", json={"content": "  likes masala chai "}, headers=auth("alice")
    )
    assert response.status_code == 200 and response.json()["content"] == "likes masala chai"


@pytest.mark.parametrize(
    "body", [{}, {"content": "   "}, {"content": "x" * 501}, {"content": "a", "x": 1}]
)
async def test_patch_rejects_invalid_bodies(
    client: TestClient, store: SQLiteMemoryStore, body: dict[str, object]
) -> None:
    memory_id = await seed(store, "alice", "likes chai")
    assert (
        client.patch(f"/memories/{memory_id}", json=body, headers=auth("alice")).status_code == 422
    )


async def test_other_users_memory_ids_are_404_for_patch_and_delete(
    client: TestClient, store: SQLiteMemoryStore
) -> None:
    memory_id = await seed(store, "alice", "alice private")
    patched = client.patch(f"/memories/{memory_id}", json={"content": "pwned"}, headers=auth("bob"))
    deleted = client.delete(f"/memories/{memory_id}", headers=auth("bob"))
    assert patched.status_code == deleted.status_code == 404
    assert [m.content for m in await store.list("alice")] == ["alice private"]


async def test_delete_one_then_404_on_repeat(client: TestClient, store: SQLiteMemoryStore) -> None:
    memory_id = await seed(store, "alice", "likes chai")
    assert client.delete(f"/memories/{memory_id}", headers=auth("alice")).status_code == 204
    assert client.delete(f"/memories/{memory_id}", headers=auth("alice")).status_code == 404


async def test_delete_all_only_clears_the_callers_memories(
    client: TestClient, store: SQLiteMemoryStore
) -> None:
    await seed(store, "alice", "a1")
    await seed(store, "alice", "a2")
    await seed(store, "bob", "b1")
    assert client.delete("/memories", headers=auth("alice")).status_code == 204
    assert await store.list("alice") == []
    assert len(await store.list("bob")) == 1


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}]
)
def test_missing_or_invalid_token_is_401(client: TestClient, headers: dict[str, str]) -> None:
    for method, url in (("GET", "/memories"), ("DELETE", "/memories"), ("DELETE", "/memories/x")):
        assert client.request(method, url, headers=headers).status_code == 401
    assert client.patch("/memories/x", json={"content": "y"}, headers=headers).status_code == 401
