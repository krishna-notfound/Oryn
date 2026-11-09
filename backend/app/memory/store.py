"""SQLite long-term memory: rows + FTS5 keyword index + sqlite-vec vector index.

One connection guarded by a lock; every blocking call runs in a worker thread so the
event loop never waits on disk. Retrieval fuses FTS5 (bm25) and vector KNN rankings with
reciprocal-rank fusion. If sqlite-vec cannot load, or no embedder is configured, the store
degrades to keyword search only. Every statement is scoped by `user_id`.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]

from app.conversation.ports import Memory, MemoryRetriever
from app.providers.base import Embedder

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 500
_RRF_K = 60  # standard reciprocal-rank-fusion damping constant
_CANDIDATES_PER_RANKER = 20
_MAX_QUERY_TERMS = 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- stable rowid shared with the FTS/vector indexes
    id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_user_idx ON memories (user_id, created_at);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, content='memories', content_rowid='seq');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.seq, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.seq, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.seq, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.seq, new.content);
END;
"""

_COLUMNS = "seq, id, content, category, confidence"
_Memories = list[Memory]  # alias: `list` is shadowed by the store method inside the class
_Vector = list[float]


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression (OR of quoted terms).

    Quoting every term means user text can never inject FTS operators or break syntax.
    """
    terms = [t for t in text.split() if any(ch.isalnum() for ch in t)][:_MAX_QUERY_TERMS]
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _to_memory(row: tuple[Any, ...]) -> Memory:
    _, id_, content, category, confidence = row
    return Memory(id_, content, category, float(confidence))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SQLiteMemoryStore(MemoryRetriever):
    """User-scoped memory store. Create once at startup, `close()` on shutdown."""

    def __init__(self, path: Path | str, embedder: Embedder | None = None) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._embedder = embedder
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._vectors = self._init_vectors()

    @property
    def vector_search_enabled(self) -> bool:
        return self._vectors and self._embedder is not None

    def _init_vectors(self) -> bool:
        """Load sqlite-vec and ensure the vector table; False means keyword-only."""
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        except (sqlite3.Error, AttributeError, OSError) as exc:
            logger.warning(
                "sqlite-vec unavailable, using keyword search only", extra={"error": str(exc)}
            )
            return False
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'memories_vec'"
        ).fetchone()
        if exists:
            return True
        if self._embedder is None:
            return False  # nothing to index yet; created once an embedder is supplied
        # user_id as partition key makes KNN filter per user inside the index, so other
        # users' vectors can never crowd out (or leak into) a user's results.
        self._conn.execute(
            "CREATE VIRTUAL TABLE memories_vec USING vec0("
            f"user_id text partition key, embedding float[{self._embedder.dimensions}])"
        )
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ writes

    async def add(
        self,
        user_id: str,
        content: str,
        category: str,
        confidence: float = 1.0,
        source_conversation_id: str | None = None,
    ) -> Memory:
        content = self._clean(content)
        vector = await self._embed_one(content)
        return await asyncio.to_thread(
            self._add, user_id, content, category, confidence, source_conversation_id, vector
        )

    async def update(self, user_id: str, memory_id: str, content: str) -> Memory | None:
        """Replace a memory's text. Returns None when the user has no such memory."""
        content = self._clean(content)
        vector = await self._embed_one(content)
        return await asyncio.to_thread(self._update, user_id, memory_id, content, vector)

    async def delete(self, user_id: str, memory_id: str) -> bool:
        return await asyncio.to_thread(self._delete, user_id, memory_id)

    async def delete_all(self, user_id: str) -> int:
        return await asyncio.to_thread(self._delete_all, user_id)

    # ------------------------------------------------------------------- reads

    async def list(self, user_id: str) -> _Memories:
        return await asyncio.to_thread(self._list, user_id)

    async def relevant(self, user_id: str, query: str, limit: int = 5) -> _Memories:
        match = fts_query(query)
        if match is None or limit <= 0:
            return []
        vector = await self._embed_one(query)
        return await asyncio.to_thread(self._relevant, user_id, match, vector, limit)

    # ------------------------------------------------------------- embeddings

    @staticmethod
    def _clean(content: str) -> str:
        content = " ".join(content.split())
        if not content:
            raise ValueError("memory content must not be empty")
        return content[:MAX_CONTENT_CHARS]

    async def _embed_one(self, text: str) -> _Vector | None:
        """Embedding failures degrade to keyword-only behaviour instead of losing the memory."""
        if not self.vector_search_enabled:
            return None
        assert self._embedder is not None
        try:
            (vector,) = await self._embedder.embed([text])
        except Exception:
            logger.warning("embedding failed, falling back to keyword search")
            return None
        if len(vector) != self._embedder.dimensions:
            logger.warning("embedding has unexpected size, ignoring it")
            return None
        return vector

    def _write_vector(self, seq: int, user_id: str, vector: Sequence[float] | None) -> None:
        if vector is None or not self._vectors:
            return
        self._conn.execute("DELETE FROM memories_vec WHERE rowid = ?", (seq,))
        self._conn.execute(
            "INSERT INTO memories_vec(rowid, user_id, embedding) VALUES (?, ?, ?)",
            (seq, user_id, sqlite_vec.serialize_float32(list(vector))),
        )

    def _drop_vectors(self, seqs: Sequence[int]) -> None:
        if self._vectors:
            self._conn.executemany("DELETE FROM memories_vec WHERE rowid = ?", [(s,) for s in seqs])

    # ----------------------------------------------------- blocking (thread) side

    def _add(
        self,
        user_id: str,
        content: str,
        category: str,
        confidence: float,
        source_conversation_id: str | None,
        vector: Sequence[float] | None,
    ) -> Memory:
        memory_id, now = uuid.uuid4().hex, _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO memories (id, user_id, content, category, confidence, "
                "source_conversation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    user_id,
                    content,
                    category,
                    confidence,
                    source_conversation_id,
                    now,
                    now,
                ),
            )
            assert cur.lastrowid is not None
            self._write_vector(cur.lastrowid, user_id, vector)
        return Memory(memory_id, content, category, confidence)

    def _update(
        self, user_id: str, memory_id: str, content: str, vector: Sequence[float] | None
    ) -> Memory | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = ? AND user_id = ? "
                f"RETURNING {_COLUMNS}",
                (content, _now(), memory_id, user_id),
            ).fetchone()
            if row is None:
                return None
            if vector is not None:
                self._write_vector(row[0], user_id, vector)
            else:
                self._drop_vectors([row[0]])  # a stale vector would mislead ranking
        return _to_memory(row)

    def _delete(self, user_id: str, memory_id: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "DELETE FROM memories WHERE id = ? AND user_id = ? RETURNING seq",
                (memory_id, user_id),
            ).fetchone()
            if row is None:
                return False
            self._drop_vectors([row[0]])
        return True

    def _delete_all(self, user_id: str) -> int:
        with self._lock, self._conn:
            rows = self._conn.execute(
                "DELETE FROM memories WHERE user_id = ? RETURNING seq", (user_id,)
            ).fetchall()
            self._drop_vectors([r[0] for r in rows])
        return len(rows)

    def _list(self, user_id: str) -> _Memories:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE user_id = ? ORDER BY seq DESC", (user_id,)
            ).fetchall()
        return [_to_memory(r) for r in rows]

    def _relevant(
        self, user_id: str, match: str, vector: Sequence[float] | None, limit: int
    ) -> _Memories:
        pool = max(limit, _CANDIDATES_PER_RANKER)
        with self._lock:
            keyword = self._conn.execute(
                "SELECT m.seq FROM memories_fts JOIN memories m ON m.seq = memories_fts.rowid "
                "WHERE memories_fts MATCH ? AND m.user_id = ? ORDER BY bm25(memories_fts) LIMIT ?",
                (match, user_id, pool),
            ).fetchall()
            semantic = (
                self._conn.execute(
                    "SELECT rowid FROM memories_vec "
                    "WHERE embedding MATCH ? AND k = ? AND user_id = ? ORDER BY distance",
                    (sqlite_vec.serialize_float32(list(vector)), pool, user_id),
                ).fetchall()
                if vector is not None
                else []
            )
            scores: dict[int, float] = {}
            for ranking in (keyword, semantic):
                for rank, (seq,) in enumerate(ranking):
                    scores[seq] = scores.get(seq, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if not scores:
                return []
            top = sorted(scores, key=lambda s: scores[s], reverse=True)[:limit]
            marks = ",".join("?" * len(top))
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE user_id = ? AND seq IN ({marks})",
                (user_id, *top),
            ).fetchall()
        by_seq = {r[0]: r for r in rows}
        return [_to_memory(by_seq[s]) for s in top if s in by_seq]
