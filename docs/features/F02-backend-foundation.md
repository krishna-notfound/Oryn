# F02 — Backend foundation + config

**Status:** IMPLEMENTED · **Branch:** `feat/F02-server` · **Phase:** 1 · **Depends on:** —

## Overview / problem
The backend needs a thin, testable composition root that wires settings, auth, persistence and the
WebSocket layer without knowing any provider or engine.

## Goals · Non-goals
- Goals: `create_app` factory, `/health`, CORS from settings, `WS /ws`, repositories behind small
  interfaces, a module-level `app` that imports with no env vars.
- Non-goals: the conversation engine, providers, memory, REST routes beyond `/health`.

## User stories
- As an operator I can probe `/health` and see the active session count.
- As a developer I can build the app in tests with fakes for every dependency.

## Functional requirements
1. `create_app(settings, conversation_factory, *, profiles=None, conversations=None, authenticator=None)`.
2. `GET /health` -> `{"status":"ok","active_sessions":n}`.
3. CORS origins from `Settings.cors_origins`.
4. Missing dependencies are built from settings; unconfigured Supabase falls back to an in-memory
   conversation repository and default profiles.
5. Module-level `app` uses `unconfigured_factory` (sends `error: not_configured`) until the real
   engine is wired.

## Non-functional requirements (latency, limits)
- One pooled `httpx.AsyncClient` (5 s timeout, 3 s connect) closed on shutdown.
- No blocking I/O on the loop (JWKS fetch runs in `asyncio.to_thread`).

## Design (classes, files, data flow)
`app/main.py` (composition root), `app/persistence/postgrest.py` (`PostgrestClient`,
`PersistenceError`), `app/persistence/conversations.py` (`ConversationRepository` ABC,
`PostgrestConversationRepository`, `InMemoryConversationRepository`),
`app/conversation/unconfigured.py`. Layering follows `docs/context/architecture.md`.

## Data / API / WebSocket changes
Adds `GET /health`. Adds `supabase/migrations/20261109000000_init.sql` (profiles, conversations,
messages, consents, RLS, `updated_at` + `handle_new_user` triggers). Not applied by this feature.

## Security
See `docs/context/security-and-safety.md`. The service-role key is used only by `PostgrestClient`.

## Failure and edge cases
Supabase unreachable: profile load degrades to defaults; conversation creation falls back to an
unpersisted uuid. Unconfigured Supabase: `PersistenceError("supabase is not configured")`.

## Acceptance criteria
- `from app.main import app` works with an empty environment.
- `/health` reflects live sessions.

## Test cases
`tests/test_session.py::test_health_reports_active_sessions`,
`::test_module_level_app_imports_without_env_and_rejects_all_tokens`,
`tests/test_persistence.py` (all, using `httpx.MockTransport`).

## Implementation notes (what actually got built, deviations)
- `ConversationRepository` is an ABC (so the in-memory and PostgREST versions are interchangeable);
  it also has `owns(conversation_id, user_id)`, needed to avoid trusting browser-sent ids.
- No new settings or dependencies were required.
- Migration deviations from "select/insert/update/delete own": `messages` has no browser insert/update
  policy (service role writes), `consents` is append-only (select + insert only).
- The SQL has not been executed against a database yet; the orchestrator applies it.

## Verification evidence
Run in `backend/` (Python 3.13.9 venv):
- `pytest -q` -> `48 passed`
- `ruff check .` -> `All checks passed!`; `ruff format --check .` -> `25 files already formatted`
- `mypy app` -> `Success: no issues found in 21 source files`
