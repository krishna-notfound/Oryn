import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from app.memory import store as store_module
from app.memory.store import MAX_CONTENT_CHARS, SQLiteMemoryStore, fts_query
from app.providers.base import Embedder
from app.providers.fakes import FakeEmbedder

ALICE, BOB = "alice", "bob"


@pytest.fixture
def store() -> Iterator[SQLiteMemoryStore]:
    s = SQLiteMemoryStore(":memory:", FakeEmbedder())
    yield s
    s.close()


@pytest.fixture
def keyword_store() -> Iterator[SQLiteMemoryStore]:
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


async def test_add_list_update_delete_roundtrip(store: SQLiteMemoryStore) -> None:
    mem = await store.add(ALICE, "Loves filter coffee", "preference", 0.9, "conv-1")
    assert (mem.content, mem.category, mem.confidence) == ("Loves filter coffee", "preference", 0.9)
    assert [m.id for m in await store.list(ALICE)] == [mem.id]

    updated = await store.update(ALICE, mem.id, "Loves masala chai")
    assert updated is not None and updated.content == "Loves masala chai"
    assert [m.content for m in await store.relevant(ALICE, "chai")][0] == "Loves masala chai"

    assert await store.delete(ALICE, mem.id) is True
    assert await store.list(ALICE) == []
    assert await store.relevant(ALICE, "chai") == []
    assert await store.delete(ALICE, mem.id) is False


async def test_update_replaces_keyword_index_entry(keyword_store: SQLiteMemoryStore) -> None:
    mem = await keyword_store.add(ALICE, "Loves filter coffee", "preference")
    await keyword_store.update(ALICE, mem.id, "Loves masala chai")
    assert await keyword_store.relevant(ALICE, "coffee") == []
    assert len(await keyword_store.relevant(ALICE, "chai")) == 1


async def test_delete_all_returns_count_and_clears_indexes(store: SQLiteMemoryStore) -> None:
    for text in ("likes cricket", "likes chess"):
        await store.add(ALICE, text, "interest")
    assert await store.delete_all(ALICE) == 2
    assert await store.list(ALICE) == []
    assert await store.relevant(ALICE, "likes") == []


async def test_content_is_normalised_capped_and_never_empty(store: SQLiteMemoryStore) -> None:
    mem = await store.add(ALICE, "  spaced \n out  ", "explicit")
    assert mem.content == "spaced out"
    long = await store.add(ALICE, "x" * (MAX_CONTENT_CHARS + 50), "explicit")
    assert len(long.content) == MAX_CONTENT_CHARS
    with pytest.raises(ValueError):
        await store.add(ALICE, "   ", "explicit")


async def test_hybrid_ranking_puts_relevant_memory_first(store: SQLiteMemoryStore) -> None:
    await store.add(ALICE, "My sister is called Priya", "personal_context")
    await store.add(ALICE, "I am learning to play the guitar", "interest")
    await store.add(ALICE, "Prefers short answers", "preference")
    results = await store.relevant(ALICE, "guitar practice")
    assert results[0].content == "I am learning to play the guitar"


async def test_vector_search_finds_memories_without_keyword_overlap(
    store: SQLiteMemoryStore,
) -> None:
    await store.add(ALICE, "learning guitar", "interest")
    await store.add(ALICE, "zzz qqq xxx", "interest")
    # "guitars" shares no FTS token with the stored text; only the vector side can match it.
    assert (await store.relevant(ALICE, "guitars"))[0].content == "learning guitar"


async def test_keyword_only_store_works_without_embedder(keyword_store: SQLiteMemoryStore) -> None:
    assert keyword_store.vector_search_enabled is False
    await keyword_store.add(ALICE, "I am learning to play the guitar", "interest")
    await keyword_store.add(ALICE, "My sister is called Priya", "personal_context")
    assert [m.content for m in await keyword_store.relevant(ALICE, "guitar")] == [
        "I am learning to play the guitar"
    ]


async def test_degrades_to_keyword_search_when_sqlite_vec_cannot_load(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken_load(_conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("extension unavailable")

    monkeypatch.setattr(store_module.sqlite_vec, "load", broken_load)
    degraded = SQLiteMemoryStore(":memory:", FakeEmbedder())
    try:
        assert degraded.vector_search_enabled is False
        assert "keyword search only" in caplog.text
        mem = await degraded.add(ALICE, "enjoys trekking", "interest")
        assert [m.id for m in await degraded.relevant(ALICE, "trekking")] == [mem.id]
        assert await degraded.delete(ALICE, mem.id) is True
    finally:
        degraded.close()


class ExplodingEmbedder(Embedder):
    name = "boom"
    dimensions = 32

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding service down")


async def test_embedding_failure_still_saves_and_finds_by_keyword() -> None:
    s = SQLiteMemoryStore(":memory:", ExplodingEmbedder())
    try:
        mem = await s.add(ALICE, "enjoys trekking", "interest")
        assert [m.id for m in await s.relevant(ALICE, "trekking")] == [mem.id]
    finally:
        s.close()


async def test_users_cannot_see_change_or_delete_each_others_memories(
    store: SQLiteMemoryStore,
) -> None:
    secret = await store.add(ALICE, "alice secret guitar plan", "personal_context")
    mine = await store.add(BOB, "bob likes guitar", "interest")

    assert [m.id for m in await store.relevant(BOB, "alice secret guitar plan")] == [mine.id]
    assert [m.id for m in await store.list(BOB)] == [mine.id]
    assert await store.update(BOB, secret.id, "hijacked") is None
    assert await store.delete(BOB, secret.id) is False
    assert await store.delete_all(BOB) == 1

    (untouched,) = await store.list(ALICE)
    assert untouched.content == "alice secret guitar plan"
    assert [m.id for m in await store.relevant(ALICE, "guitar")] == [secret.id]


async def test_vector_results_are_scoped_per_user_even_with_many_other_users_vectors(
    store: SQLiteMemoryStore,
) -> None:
    for i in range(30):
        await store.add(BOB, f"guitar {i}", "interest")
    mine = await store.add(ALICE, "guitar", "interest")
    assert [m.id for m in await store.relevant(ALICE, "guitars", limit=5)] == [mine.id]


@pytest.mark.parametrize(
    "hostile",
    ['"unterminated', "foo AND", "NEAR(a b)", "col:value", "a* OR -b", "(((", "x'; DROP TABLE m"],
)
async def test_hostile_query_text_cannot_break_fts_syntax(
    store: SQLiteMemoryStore, hostile: str
) -> None:
    await store.add(ALICE, "plain memory", "preference")
    assert isinstance(await store.relevant(ALICE, hostile), list)
    assert len(await store.list(ALICE)) == 1


def test_fts_query_quotes_every_term_and_rejects_punctuation_only() -> None:
    assert fts_query('say "hi" NEAR') == '"say" OR """hi""" OR "NEAR"'
    assert fts_query("  ?! -- ") is None


async def test_non_latin_text_is_searchable(store: SQLiteMemoryStore) -> None:
    await store.add(ALICE, "मुझे चाय पसंद है", "preference")
    assert len(await store.relevant(ALICE, "चाय")) == 1


async def test_data_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "memory.db"
    first = SQLiteMemoryStore(path, FakeEmbedder())
    await first.add(ALICE, "likes mountains", "interest")
    first.close()
    second = SQLiteMemoryStore(path, FakeEmbedder())
    try:
        assert [m.content for m in await second.relevant(ALICE, "mountains")] == ["likes mountains"]
    finally:
        second.close()
