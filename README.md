# Oryn

**An AI companion you can actually talk to.** Realtime, voice-first and multilingual (Hindi, English,
Hinglish), with natural barge-in: interrupt mid-sentence and Oryn stops and listens.

Built for the Gnani AI Internship Challenge.

```
Mic -> Gnani Prisma (STT) -> Sarvam LLM -> Gnani Timbre (TTS) -> Speaker
        streaming words        streams tokens     sentence-by-sentence audio
```

The first sentence is spoken while the rest is still being generated; speaking over it cancels
the whole chain (STT/LLM/TTS) and drops stale audio via a per-turn generation id.

## Architecture

```
 Browser (Next.js on Vercel)                       Backend (FastAPI, Docker on EC2 ap-south-1)
 +--------------------------+                      +----------------------------------------+
 | Supabase Auth            |                      | Caddy  :443 (auto HTTPS)               |
 | AudioWorklet mic capture |   wss:// /ws         |   -> FastAPI app.main                 |
 | playback queue + VAD     | <------------------> |      websocket/  session + protocol    |
 +--------------------------+  JSON + binary audio |      conversation/ engine, context     |
                                                   |      voice/  state machine, barge-in   |
 Supabase: Auth + Postgres (RLS)                   |      providers/ base + adapters        |
   profiles, preferences, conversations            |      memory/ SQLite + FTS5 + vectors   |
                                                   +-------------------+--------------------+
                                                                       |
                                  Gnani Prisma STT . Sarvam LLM . Gnani Timbre TTS
```

Layering (imports point down): `websocket -> conversation -> voice/memory/tools/safety -> providers/base <- adapters`.
Vendor formats never leave `backend/app/providers/`. Details: [architecture](docs/context/architecture.md),
[voice pipeline](docs/context/voice-pipeline.md), [WebSocket contract](docs/context/websocket-contract.md).

## Where Gnani is integrated

| Capability | Code | Notes |
| ---------- | ---- | ----- |
| Speech-to-text, **Gnani Prisma** | `backend/app/providers/gnani_prisma.py` | streaming WebSocket, code-mixed output |
| Text-to-speech, **Gnani Timbre** | `backend/app/providers/gnani_timbre.py` | sentence-level streaming audio |
| Contracts both implement | `backend/app/providers/base.py` | `fakes.py` gives offline tests |

Prisma and Timbre are the default providers; the LLM (Sarvam) sits behind the `LanguageModel` port.
API facts used are in [providers-gnani-research](docs/context/providers-gnani-research.md).

## Quickstart

Backend (Python 3.12+):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                     # PROVIDER_MODE=fake runs offline, no keys
uvicorn app.main:build_app --factory --reload --port 8000
pytest -q -m "not live"
```

Web:
```bash
cd apps/web && npm install && npm run dev                # http://localhost:3000
```

Environment variables are documented in `backend/.env.example` and `backend/app/config.py`
(the only place that reads the environment). Secrets stay on the backend; the browser gets public keys only.

## CI/CD

| Stage | What happens |
| ----- | ------------ |
| Pull request / push | `ruff`, `mypy --strict`, `pytest -m "not live"`, `pip-audit`; web lint + `tsc` + build; Docker build; gitleaks |
| Merge to `main` | after CI passes: image to `ghcr.io/krishna-notfound/oryn-backend:<sha>`, ssh deploy to EC2, health-gated rollout with automatic rollback, HTTPS smoke test |
| Frontend | Vercel Git integration (`apps/web`) |

Workflows: [`ci.yml`](.github/workflows/ci.yml), [`deploy.yml`](.github/workflows/deploy.yml). Infra: [`infra/`](infra).

## Docs

[Project overview](docs/context/project-overview.md) .
[Deployment](docs/context/deployment.md) .
[Testing](docs/context/testing.md) .
[Code standards](docs/context/code-standards.md) .
[Workflow](docs/context/workflow.md) .
[Features](docs/features/README.md) .
[Decisions](docs/decisions)
