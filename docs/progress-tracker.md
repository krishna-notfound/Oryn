# Progress tracker

Reflect the **actual** state, not the intended one. Update after every merge or decision.

## Current phase

Phase 1 → 2: contracts are on `main`; parallel feature build in worktrees (deadline 2026-11-10).

## Active worktrees

| Worktree | Branch | Feature(s) | Owner |
| -------- | ------ | ---------- | ----- |
| `.worktrees/F02-server` | `feat/F02-server` | F02 config/app, F03 auth+profile, F05 WS session, Supabase migration | Sonnet |
| `.worktrees/F06-providers` | `feat/F06-providers` | F06 Prisma STT, F08 Timbre TTS, Sarvam LLM adapter, registry | Sonnet |
| `.worktrees/F07-engine` | `feat/F07-engine` | F07 engine, F09 state machine + barge-in, F14 safety, F15 latency | Sonnet |
| `.worktrees/F12-memory-tools` | `feat/F12-memory-tools` | F12 SQLite memory + extractor + REST, F13 tools | Sonnet |
| `.worktrees/F01-web` | `feat/F01-web` | F01 web foundation, F04 onboarding, F10 chat, F08 playback (client) | Sonnet |
| `.worktrees/F16-devops` | `feat/F16-devops` | F16 Docker, CI, deploy, README, testing/deployment docs | Sonnet |

Merge order: F06 → F07 → F12 → F02 (then Opus wires `app/main.py` composition root) → F16 → F01.
Supabase public schema is empty (checked 2026-11-09); migration applied by Opus after F02 merges.

## Completed

- 2026-11-09 Repo renamed Saathi → Oryn (`krishna-notfound/Oryn`), branch `main`.
- 2026-11-09 Docs system: CLAUDE.md, docs/context, docs/features, docs/decisions.
- 2026-11-09 Gnani Prisma/Timbre API researched → `docs/context/providers-gnani-research.md`.
- 2026-11-09 Shared contracts: `providers/base.py`, `providers/fakes.py`,
  `websocket/protocol.py`, `conversation/ports.py`, `voice/state.py` (4 contract tests pass).
- 2026-11-09 Stitch UI exported to `design/stitch/` (7 screens + DESIGN.md).

## Next up

1. Merge parallel features F02/F03/F05, F06/F07/F08, F09/F15, F12/F13, F01/F04/F10, F16.
2. Integration: real Gnani + Sarvam keys in `backend/.env`, end-to-end demo run, latency report.
3. Deploy: EC2 + Vercel.

## Open questions

- Sarvam chat model id and exact endpoint/headers — verify in docs.sarvam.ai (F07).
- Timbre voice id to use per language (pick from research file voice list).
- Embeddings provider for memory (Sarvam has none verified → OpenAI-compatible, configurable).

## Architecture decisions

- ADR-001 Gnani Prisma/Timbre as default STT/TTS; Sarvam LLM.
- ADR-002 One persistent WS per conversation; generation-id-tagged binary audio.
- ADR-003 SQLite + FTS5 + sqlite-vec for memory on EC2 volume.

## Session notes

- No local Docker or uv on the dev machine; CI builds the image. Python 3.13 local, 3.12 in Docker.
- `backend/.venv` exists locally (gitignored).
