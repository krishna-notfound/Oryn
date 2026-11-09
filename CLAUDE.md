# Oryn — project instructions

Oryn (formerly Saathi) is a realtime, voice-first, multilingual AI companion built for the
**Gnani AI Internship Challenge**. Pipeline: browser mic → WebSocket → **Gnani Prisma STT** →
**Sarvam conversational LLM** (the brain) → **Gnani Timbre TTS** → browser speaker, with barge-in.
Promise: *"An AI companion you can actually talk to."* Build less; make the core exceptional.
The backend is the showcase: it must read like senior production code.

## Start every session here

1. Read `docs/progress-tracker.md` — current phase, active worktrees, next steps.
2. Read `docs/features/README.md` — every feature, its status and branch.
3. Open **only** the context docs your task needs (table below). Keep context small.

| Task touches…                            | Read                                                 |
| ---------------------------------------- | ---------------------------------------------------- |
| Product scope, demo script               | `docs/context/project-overview.md`                   |
| Repo layout, boundaries, invariants      | `docs/context/architecture.md`                       |
| STT→LLM→TTS flow, states, barge-in       | `docs/context/voice-pipeline.md`                     |
| Browser ↔ backend messages               | `docs/context/websocket-contract.md` + `backend/app/websocket/protocol.py` |
| Gnani / Sarvam / embedding adapters      | `docs/context/providers.md`, `docs/context/providers-gnani-research.md` |
| Supabase schema, RLS, SQLite memory      | `docs/context/data-and-memory.md`                    |
| Auth, privacy, safety rules              | `docs/context/security-and-safety.md`                |
| Frontend screens, palette                | `docs/context/ui-context.md`, `design/stitch/`       |
| Code style (Python + TS)                 | `docs/context/code-standards.md`                     |
| How work is planned/branched/merged      | `docs/context/workflow.md`                           |
| Tests, CI, Docker, EC2                   | `docs/context/testing.md`, `docs/context/deployment.md` |
| Why a decision was made                  | `docs/decisions/ADR-*.md`                            |

## Non-negotiables

- **Gnani Prisma (STT) and Gnani Timbre (TTS) are the default voice providers.** Never swap
  them out as default. **LLM = Sarvam conversational model** behind the `LanguageModel` port.
- **Never invent provider API details.** Use only what is verified in
  `docs/context/providers-gnani-research.md` / Sarvam's official docs; mark the rest UNVERIFIED.
- Vendor code stays inside `backend/app/providers/*`. Everything else depends on
  `providers/base.py` and `conversation/ports.py` only.
- Secrets live in env vars on the backend only — never in the browser bundle, git, or logs.
- Verify the Supabase JWT on every WebSocket; never trust a user id sent by the browser.
- Memory is user-scoped and consent-gated. Memory extraction never blocks audio.
- No raw audio or conversation text in logs by default.
- Never claim a test passed or a feature works without running it. `VERIFIED` needs evidence.

## How we build (details: `docs/context/workflow.md`)

- Opus = orchestrator/reviewer. Implementation subagents run on **Sonnet 5.5**.
- One feature = one spec in `docs/features/` → one **git worktree** in `.worktrees/<id>-<slug>`
  on branch `feat/<id>-<slug>` → PR to `main` → CI green → merge.
- **ponytail** plugin is on: simplest code that works, stdlib first, no unrequested abstractions.
- Readable OOP Python: small classes with one job, explicit names, docstrings on public seams.
- TDD for backend logic. Use fakes from `backend/app/providers/fakes.py` in tests.
- After every merge: update the feature status and `docs/progress-tracker.md`.
- If you change architecture, scope or standards, update the matching context doc first.

## Commands

```bash
# backend (Python 3.12+)
cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/pytest -q
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/mypy app
.venv/Scripts/uvicorn app.main:app --reload --port 8000   # PROVIDER_MODE=fake runs offline

# frontend
cd apps/web && npm install && npm run dev
```

## Frontend note — this is NOT the Next.js you know

`apps/web` uses a recent Next.js with breaking changes. Read the relevant guide in
`apps/web/node_modules/next/dist/docs/` before writing Next.js code and heed deprecations.

## External resources

- GitHub `krishna-notfound/Oryn` · Stitch project `4332495550980426447` (exported to `design/stitch/`)
- Supabase project ref `bfyrnqriusicvgwxogwr` (MCP in `.mcp.json`) · Vercel MCP connected
- Gnani docs https://docs.gnani.ai · Sarvam docs https://docs.sarvam.ai
