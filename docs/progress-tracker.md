# Progress tracker

Reflect the **actual** state, not the intended one. Update after every merge or decision.

## Current phase

Phase 1 → 2: contracts are on `main`; parallel feature build in worktrees (deadline 2026-11-10).

## Active worktrees

| Worktree | Branch | Feature(s) | Owner |
| -------- | ------ | ---------- | ----- |
| see `git worktree list` | `feat/*` | see `docs/features/README.md` | Sonnet subagents, Opus reviews |

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
