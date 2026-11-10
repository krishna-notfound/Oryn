# Contributing to Oryn

Thanks for wanting to help. Oryn is a latency-sensitive realtime system, so the bar is: correct under
concurrency, measured, and simple.

## Ground rules

- **Open an issue first** for anything bigger than a small fix, so we can agree on the approach.
- **Measure claims.** A change that says it is faster includes `scripts/benchmark_pipeline.py` output
  (p50/p95) before and after. A change that touches cancellation includes a test that interrupts it.
- **Keep vendors in adapters.** Provider-specific code lives only in `backend/app/providers/`; everything
  else depends on `providers/base.py`.
- **Never commit secrets.** Keys go in `backend/.env` / `apps/web/.env.local` (both gitignored).

## Setup

```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
PROVIDER_MODE=fake uvicorn app.main:build_app --factory --reload --port 8000

# web
cd apps/web && npm install && npm run dev
```

`PROVIDER_MODE=fake` runs the whole pipeline offline with deterministic providers, so you can work
without API keys.

## Before you open a pull request

```bash
cd backend
ruff check . && ruff format --check . && mypy app
pytest -q -m "not live"

cd ../apps/web
npm run lint && npx tsc --noEmit && npm test && npm run build
```

CI runs the same checks plus a Docker build, a secret scan and a dependency audit.

## Conventions

- Branches: `feature/<what-it-does>`, `fix/<what-it-fixes>`, `chore/<what-it-changes>`.
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) (`feat(voice): …`, `fix(ws): …`).
- Python: typed (`mypy --strict` clean), async all the way, every background task owned and cancelled,
  every queue bounded, never swallow `asyncio.CancelledError`.
- TypeScript: strict, audio/realtime logic in small classes under `lib/`, pure cores with `node --test` tests.
- Comments explain *why*, not *what*.

## Reporting bugs

Include what you said (or typed), the language, what you heard, and — if you can — the `turn.metrics`
events from the browser console. Never paste API keys or access tokens.

## Code of conduct

Be kind, be specific, assume good intent.
