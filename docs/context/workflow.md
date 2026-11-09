# Development workflow

Spec-driven, parallel, verified.

## Roles

- **Opus (main session)**: owns contracts (`providers/base.py`, `conversation/ports.py`,
  `websocket/protocol.py`), writes specs/plans, dispatches agents, reviews diffs, merges.
- **Sonnet 5.5 subagents**: implement one feature each inside its own git worktree.

## Lifecycle of a feature

1. Spec in `docs/features/Fxx-*.md` (from `_TEMPLATE.md`) → APPROVED.
2. `git worktree add .worktrees/Fxx-slug -b feat/Fxx-slug main`.
3. Subagent implements with TDD, keeps `pytest`, `ruff`, `mypy` green, commits small.
4. Subagent fills *Implementation notes* + *Verification evidence* in the spec → IMPLEMENTED.
5. Opus reviews (correctness, contract adherence, ponytail leanness), pushes branch, opens PR.
6. CI green → squash-merge → `git worktree remove` → update `docs/features/README.md` and
   `docs/progress-tracker.md`.
7. VERIFIED after end-to-end run with real providers (evidence linked).

## Rules

- Never edit a shared contract file from a feature branch; ask the orchestrator.
  Additive changes to `fakes.py` are allowed.
- One feature unit per worktree; don't touch files owned by another active worktree.
- Missing requirement → add to *Open questions* in the progress tracker, pick the conservative
  option, note it in the spec.
- Ask before destructive operations or changes that contradict an APPROVED spec.
- Real-provider tests are marked `@pytest.mark.live` and skipped in CI.
