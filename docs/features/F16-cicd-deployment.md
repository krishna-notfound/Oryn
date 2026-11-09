# F16 — CI/CD + Docker + deployment

**Status:** IMPLEMENTED · **Branch:** `feat/F16-devops` · **Phase:** 2 · **Depends on:** F02 (`app.main:app`, `/health`), F01 (`apps/web`)

## Overview / problem
Oryn needs a delivery pipeline that is secure, lean and actually works on a small EC2 box:
validate every change, publish a backend image, deploy it safely with rollback, and document
how the frontend (Vercel) and backend (EC2) fit together.

## Goals · Non-goals
Goals: reproducible backend image; CI gates (lint, types, tests, vulnerability and secret scans,
web build, image build); automatic deploy of `main` to EC2 over HTTPS/WSS; rollback.
Non-goals: Kubernetes, multi-instance/blue-green, automatic DB migrations, Vercel workflow
(Vercel's Git integration deploys the frontend), observability stack beyond `docker logs`.

## User stories
- As a developer, a PR shows red/green for backend, web, docker and secrets checks.
- As the owner, merging to `main` ships the tested commit to production without manual steps.
- As the owner, a bad release rolls itself back and the workflow goes red.

## Functional requirements
1. `backend/Dockerfile`: multi-stage, non-root, `/data` volume, stdlib `HEALTHCHECK`, single uvicorn worker.
2. `infra/docker/docker-compose.prod.yml` + `Caddyfile`: backend + Caddy with automatic HTTPS; only 80/443 published.
3. `infra/scripts/ec2-bootstrap.sh` (idempotent) and `infra/scripts/deploy.sh` (pull, up, health wait, rollback).
4. `.github/workflows/ci.yml`: `backend`, `web` (skipped until `apps/web/package.json` exists), `docker`, `secrets`.
5. `.github/workflows/deploy.yml`: after CI success on `main`, push `ghcr.io/krishna-notfound/oryn-backend:<sha>` + `:latest`; deploy over SSH if EC2 secrets exist; smoke test `/health`.
6. Docs: `docs/context/deployment.md`, `docs/context/testing.md`, root `README.md`.

## Non-functional requirements (latency, limits)
Backend container limited to 1.2 GB (2 GB host + 1 GB swap); Caddy 128 MB. CI jobs have timeouts and
cancel superseded runs. Deploy waits at most 120 s for health.

## Design (classes, files, data flow)
```
CI (pull_request, push main) -- success on main --> Deploy (workflow_run)
   image job: build+push GHCR (:sha, :latest)
   deploy job: scp stack files -> ssh deploy.sh <sha> -> curl https://$ORYN_DOMAIN/health
```
- `workflow_run` (not `needs`) so CI is not duplicated; the image is built from CI's `head_sha`.
- Plain `ssh`/`scp` with a key file instead of third-party actions: smaller supply-chain surface.
- gitleaks via pinned CLI + checksum verification (the action needs a license for orgs).
- `deploy.sh` stores state in `/opt/oryn/.env` (`IMAGE_TAG`, `ORYN_DOMAIN`); rollback = rewrite tag + `up -d`.
- uvicorn `--forwarded-allow-ips '*'` because the client IP seen by uvicorn is Caddy's container address; port 8000 is not published.

## Data / API / WebSocket changes
None. Requires `GET /health` returning 200 and the app at `app.main:app` (F02).

## Security
Least-privilege `permissions` (`contents: read`; `packages: write` only on the image job). Secrets only
as GitHub secrets / `backend.env` (mode 600). Non-root container. No secret is echoed. SSH is key-only
with host-key checking. `pip-audit` and gitleaks block merges. Migrations are never run automatically.

## Failure and edge cases
- EC2 secrets missing: image still pushed, deploy skipped with a notice.
- Unhealthy release: rollback to the previous tag, exit non-zero. No previous tag: fail loudly.
- Pull failure: running containers untouched, tag file restored via rollback path.
- First deploy: Caddy certificate issuance may delay the smoke test; it retries for about a minute.
- Concurrent deploys are serialised (`concurrency: deploy`, no cancel).

## Acceptance criteria
- [ ] CI is green on a PR with the backend as it stands.
- [ ] Merge to `main` pushes both image tags to GHCR.
- [ ] With secrets set, the server serves the new sha and `/health` answers over HTTPS.
- [ ] A deliberately broken image triggers rollback and a red run.
- [ ] `wss://<domain>/ws` works from the Vercel-hosted page.

## Test cases
Manual, on first real run: broken-image rollback drill; rerun `ec2-bootstrap.sh` (idempotence);
deploy with secrets unset (skip path).

## Implementation notes (what actually got built, deviations)
Built as specified. Choices: Caddy over nginx+certbot (one file, automatic certs); `name: oryn` pinned
in compose so the data volume is `oryn_oryn-data`; `app/` is copied into the runtime image in addition
to the installed venv copy so any non-`.py` package files are present; optional `EC2_KNOWN_HOSTS`
secret for pinned SSH host keys; `production` GitHub environment on the deploy job.
The `NEXT_PUBLIC_*` names in the web CI build are assumptions to be matched with `apps/web`.

## Verification evidence
Honest status: **not run end to end.**
- YAML of both workflows and the compose file parsed with PyYAML; workflows linted with `actionlint` 1.7.7 (no findings); `bash -n` on both scripts.
- gitleaks and docker compose release asset names/checksum files confirmed to exist at the pinned versions.
- **Docker image NOT built locally** (Docker is not installed on the dev machine); compose stack NOT started; workflows NOT executed on GitHub; EC2 scripts NOT run. Status moves to VERIFIED after the first green CI run and a real deploy with evidence linked here.
