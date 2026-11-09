# F12 — Persistent memory + consent

**Status:** IMPLEMENTED · **Branch:** `feat/F12-memory-tools` · **Phase:** 3 · **Depends on:** F07 (engine `after_turn` hook, `MemoryRetriever` port), F03 (JWT verification)

## Overview / problem
Oryn should remember durable facts about a user (preferences, interests, goals) across sessions, but
only with consent, only for that user, and without ever delaying audio.

## Goals · Non-goals
- Goals: SQLite store with hybrid retrieval; background LLM extraction; user-facing list/edit/delete API.
- Non-goals: wiring into the engine/app factory (F07/F05 own that), the consent UI (F04), the
  Supabase `consents` audit rows, the embedding-provider adapter (F06).

## User stories
- As a user I can switch memory on or off; off means nothing is retrieved or extracted.
- As a user I can see, correct and delete everything Oryn remembers about me.
- As a user, sensitive facts are only stored if I say "remember this" / "yaad rakhna".

## Functional requirements
1. `SQLiteMemoryStore` implements `MemoryRetriever`: add / update / delete / delete_all / list / relevant.
2. `relevant()` = FTS5 bm25 top-20 + sqlite-vec KNN top-20, fused with reciprocal-rank fusion, top `limit` (default 5).
3. `MemoryExtractor(llm, store, embedder)` is awaited as `after_turn(user, transcript)`; no-op unless `user.memory_enabled`.
4. Candidates `[{content, category, confidence}]` are kept only if confidence >= 0.7 and the category is one of
   preference / interest / goal / project / personal_context / explicit. Sensitive categories
   (health, religion, sexuality, finances, credentials) are kept only when the user's message contains
   remember / yaad rakh. Near-duplicates (token overlap >= 0.7, or cosine >= 0.95 when an embedder is given) are skipped.
5. REST: `GET /memories`, `PATCH /memories/{id}`, `DELETE /memories/{id}`, `DELETE /memories`; a Bearer token is mapped to a verified user id by an injected `authenticate`.

## Non-functional requirements
- Never blocks the loop: all SQLite work runs in `asyncio.to_thread`, WAL mode, one connection behind a lock.
- Extraction is fire-and-forget from the engine; it swallows every error and times out after 20 s.
- Memory text capped at 500 chars; at most 5 candidates per turn.

## Design (classes, files, data flow)
- `backend/app/memory/store.py` — `SQLiteMemoryStore`, `fts_query()` (quotes every term, so user text cannot inject FTS syntax).
- `backend/app/memory/extractor.py` — `MemoryExtractor`, `parse_candidates()` (tolerates code fences and prose).
- `backend/app/memory/api.py` — `build_memory_router(store, authenticate)`.
- Flow: engine finishes reply -> `after_turn` task -> LLM extracts JSON -> filters -> `store.add` (embeds first; an embedding failure still saves the row, keyword-only).

## Data / API / WebSocket changes
SQLite schema as in `data-and-memory.md` (deviations noted there). New REST routes above.
Responses: `{id, content, category, confidence}`; PATCH body `{content}` (1-500 chars); deletes return 204; foreign ids return 404.

## Security
- Every statement filters by `user_id`; the vec index uses `user_id` as a partition key so KNN is per-user inside the index.
- `user_id` comes only from `authenticate(token)`, never from the request body.
- Extraction logs only counts; no transcript or memory text in logs.
- The extractor prompt marks the exchange as data and the LLM output is strictly parsed and validated.

## Failure and edge cases
- sqlite-vec fails to load or no embedder: warning plus keyword-only retrieval.
- Embedder error or wrong dimension: memory saved without a vector, query falls back to keywords.
- Malformed or hostile LLM output: parsed defensively, junk yields no memories.
- Updating a memory without a new vector removes the stale vector.
- Existing vec table created with a different dimension than the embedder: sqlite-vec refuses the vector writes (see open questions).

## Acceptance criteria
- User B can never read, update or delete user A's memories by any path (list, relevant, REST, update, delete_all).
- Memory disabled: the extractor never calls the LLM.
- Sensitive facts are stored only on explicit request.

## Test cases
`tests/test_memory_store.py` (21), `tests/test_memory_extractor.py` (20), `tests/test_memory_api.py` (12).
Store: CRUD, hybrid ranking, vector-only match, FTS-only fallback, sqlite-vec load failure, embedder failure,
user isolation (including 30 other-user vectors), hostile FTS input, Devanagari search, persistence across reopen.
Extractor: consent, fenced JSON, confidence, sensitive categories, explicit Hinglish request, dedupe, LLM errors, garbage output.
API: auth (401), 404 for foreign ids, body validation, delete-all scoping.

## Implementation notes (what actually got built, deviations)
- `memories` has `seq INTEGER PRIMARY KEY AUTOINCREMENT` plus `id TEXT UNIQUE` (rowids of a TEXT-PK table can change on VACUUM and would desync FTS/vec).
- `memories_vec` is `vec0(user_id text partition key, embedding float[N])`.
- `source_conversation_id` is stored, but the `after_turn` signature carries no conversation id, so the extractor passes `None`.
- `api.py` omits `from __future__ import annotations` so FastAPI can resolve its closure-local `UserId` dependency alias.
- Added deps: `sqlite-vec`, `tzdata` (zoneinfo data on Windows and slim images; used by F13).

## Verification evidence
Run in `backend/` on 2026-11-09 (Python 3.13.9, SQLite 3.51.0, sqlite-vec 0.1.9):
- `pytest -q` -> `154 passed`
- `ruff check .` -> `All checks passed!`; `ruff format --check .` -> `28 files already formatted`
- `mypy app` -> `Success: no issues found in 21 source files`

Not yet verified: a real embeddings provider, wiring into the live engine, concurrent load.
