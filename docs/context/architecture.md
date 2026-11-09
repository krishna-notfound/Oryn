# Architecture

"A simple app on a sophisticated backend." The browser is thin; the backend owns the pipeline.

```
apps/web (Next.js, Vercel)          backend (FastAPI, Docker on EC2 ap-south-1)
 ├ Supabase auth (client)            ├ app/main.py            app factory, /health, /ws
 ├ AudioWorklet capture              ├ app/config.py          Settings from env
 ├ playback queue + client VAD       ├ app/auth/              Supabase JWT verification
 └ WS client ─────── wss ──────────► ├ app/websocket/         protocol.py, session.py
                                     ├ app/conversation/      ports.py, engine, context builder
                                     ├ app/voice/             state machine, segmenter, latency
                                     ├ app/providers/         base.py + Gnani/LLM adapters
                                     ├ app/memory/            SQLite + FTS5 + sqlite-vec
                                     ├ app/tools/             registry + built-in tools
                                     ├ app/safety/            crisis detection, prompt rules
                                     └ app/observability/     structured logs, metrics
Supabase: auth + Postgres (profiles, preferences, conversations, messages, consents) with RLS
```

## Layering (imports only point down)

```
websocket  →  conversation  →  voice, memory, tools, safety  →  providers/base  ←  adapters
```
- `websocket/session.py` knows the `Conversation` port only.
- `conversation` knows `providers/base.py`, never an adapter.
- Adapters are chosen once in `providers/registry.py`.

## Storage

| Data | Where |
| ---- | ----- |
| Identity, profile, preferences, consents, conversation + message metadata | Supabase Postgres (RLS) |
| Extracted long-term memories + embeddings | SQLite on EC2 persistent volume (`/data/memory.db`) |
| Raw audio | nowhere (not retained) |

The backend writes Supabase with the service-role key **only** after verifying the user's JWT,
and always filters by the verified `user_id`.

## Invariants

1. Vendor formats never leave `providers/`.
2. No blocking I/O on the event loop; CPU work is tiny (no local models).
3. Memory extraction runs after the reply, in the background.
4. Every audio frame carries a generation id; stale ones are dropped.
5. Every user-owned query is scoped by the verified user id.
6. Bounded everything: queues, context window (last 12 turns), tool calls (≤3), audio frames.

## Repo layout

```
apps/web/            Next.js frontend
backend/             FastAPI app + tests
design/stitch/       Stitch export (HTML + PNG per screen, DESIGN.md)
docs/                context/, features/, decisions/, plans/, progress-tracker.md
infra/               docker, scripts
supabase/migrations/ SQL migrations
.github/workflows/   ci.yml, deploy.yml
```
