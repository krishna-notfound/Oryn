# Deployment

Backend: Docker on one EC2 instance (x86_64, 2 GB, ap-south-1) behind Caddy (automatic HTTPS).
Frontend: Vercel (`apps/web`). Database/auth: Supabase. Images: GHCR.

```
push to main -> CI (ci.yml) -> success -> Deploy (deploy.yml)
   build image -> ghcr.io/krishna-notfound/oryn-backend:<sha> + :latest
   scp compose/Caddyfile/deploy.sh -> /opt/oryn  ->  ssh deploy.sh <sha>
   deploy.sh: pull, up -d, wait healthy, else roll back  ->  smoke test https://$ORYN_DOMAIN/health
```

Frontend deploys are handled by Vercel's own Git integration (no workflow). DB migrations are
**never** run automatically; apply them by hand (Supabase MCP / dashboard) before deploying code that needs them.

## One-time EC2 setup

1. **Launch**: Amazon Linux 2023 (or Ubuntu 22.04/24.04), x86_64, 2 GB RAM (t3.small), 20 GB gp3 in `ap-south-1`.
2. **Security group inbound**: `22` (key-only SSH; GitHub-hosted runners have no fixed IP range, so it
   cannot be allowlisted to them. Allow your own IP plus the runners, or accept key-only exposure),
   `80` and `443` from `0.0.0.0/0`. Nothing else. Port 8000 is never published.
3. **Elastic IP**: attach one so the address survives stop/start.
4. **Bootstrap** (installs Docker + compose plugin, creates `/opt/oryn`, adds 1 GB swap):
   ```bash
   scp infra/scripts/ec2-bootstrap.sh ec2-user@<ip>:/tmp/ && ssh ec2-user@<ip> 'sudo bash /tmp/ec2-bootstrap.sh'
   ```
   Idempotent: safe to rerun. Log out/in afterwards so the `docker` group applies.
5. **Domain**: point an A record at the Elastic IP, or use a free hostname
   `<ip-with-dashes>.sslip.io` (e.g. `13-232-10-20.sslip.io`); Let's Encrypt issues certs for it.
   Then on the box: `echo "ORYN_DOMAIN=13-232-10-20.sslip.io" > /opt/oryn/.env`
   (`deploy.sh` later adds `IMAGE_TAG` to the same file and preserves this line).
6. **`/opt/oryn/backend.env`** (`chmod 600`, owned by the deploy user, never committed):
   ```
   APP_ENV=production
   PROVIDER_MODE=live
   CORS_ORIGINS=["https://<your-vercel-domain>"]
   SUPABASE_URL=https://bfyrnqriusicvgwxogwr.supabase.co
   SUPABASE_JWT_SECRET=
   SUPABASE_SERVICE_ROLE_KEY=
   GNANI_API_KEY=
   GNANI_STT_MODEL=prisma-v3.0
   GNANI_TTS_MODEL=timbre-v2.5
   GNANI_TTS_VOICE=
   SARVAM_API_KEY=
   LLM_MODEL=
   EMBEDDINGS_BASE_URL=
   EMBEDDINGS_API_KEY=
   EMBEDDINGS_MODEL=
   ```
   `CORS_ORIGINS` is a `list[str]` in `app/config.py`, so pydantic-settings expects a JSON array.
   Leave `SUPABASE_JWT_SECRET` empty to verify via JWKS. `MEMORY_DB_PATH=/data/memory.db` is set by
   compose (volume `oryn_oryn-data`); don't override it.
7. **GHCR auth on the box**: the first push creates a *private* package. Either make it public
   (GitHub -> Packages -> oryn-backend -> Settings -> Change visibility; simplest, the image holds no
   secrets) or log in once as the deploy user with a classic PAT that has only `read:packages`:
   `echo "$PAT" | docker login ghcr.io -u krishna-notfound --password-stdin`.
8. **Deploy SSH key**: `ssh-keygen -t ed25519 -f oryn_deploy -N ""`; append `oryn_deploy.pub` to
   `~/.ssh/authorized_keys` on the box; the private key goes into the `EC2_SSH_KEY` secret.
   Recommended: put `ssh-keyscan <host>` output into the `EC2_KNOWN_HOSTS` secret (otherwise the
   workflow trusts whatever key the host presents at run time).
9. Merge to `main`; the Deploy workflow does the rest. The first deploy may take a minute longer
   while Caddy obtains the certificate.

## GitHub configuration

| Name | Kind | Required | Purpose |
| ---- | ---- | -------- | ------- |
| `EC2_HOST` | secret | for deploy | Elastic IP or DNS name of the server |
| `EC2_USER` | secret | for deploy | SSH user (`ec2-user` / `ubuntu`) |
| `EC2_SSH_KEY` | secret | for deploy | Private deploy key (ed25519) |
| `EC2_KNOWN_HOSTS` | secret | recommended | Pinned host key line(s); falls back to ssh-keyscan |
| `ORYN_DOMAIN` | variable (or secret) | for smoke test | Public hostname, e.g. `13-232-10-20.sslip.io` |

If any of the three required secrets is missing the Deploy workflow still builds and pushes the image
and prints a notice instead of failing. The automatic `GITHUB_TOKEN` (`packages: write`, image job
only) pushes to GHCR. The `deploy` job runs in the `production` GitHub environment: add required
reviewers there if you want manual approval.

## Vercel (`apps/web`)

- Import `krishna-notfound/Oryn`, **Root Directory = `apps/web`**, framework Next.js, Node 22.
- Environment variables (Production + Preview). Public values only, nothing secret:

  | Variable | Value |
  | -------- | ----- |
  | `NEXT_PUBLIC_SUPABASE_URL` | `https://bfyrnqriusicvgwxogwr.supabase.co` |
  | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase publishable (anon) key |
  | `NEXT_PUBLIC_BACKEND_WS_URL` | `wss://<ORYN_DOMAIN>/ws` |

  These names match the dummy values in CI's web build; if `apps/web` reads different ones, change both.
- Add the Vercel production domain to `CORS_ORIGINS` in `backend.env` and to the Supabase Auth redirect URLs.
- The page is https, so the WebSocket must be `wss://`; that is why Caddy terminates TLS.

## Rollback

Automatic: `deploy.sh` restores the previous tag if the new container is not healthy within 120 s and
exits non-zero (the workflow goes red).

Manual, to any earlier commit (tags are full commit SHAs):
```bash
ssh <user>@<host> 'cd /opt/oryn && ./deploy.sh <good-sha>'
```
It pulls that tag from GHCR and replaces the running container. Memory data survives (named volume).

## Monitoring and operations

```bash
cd /opt/oryn
docker compose -f docker-compose.prod.yml ps                        # state + health
docker compose -f docker-compose.prod.yml logs -f --tail 100 backend
docker compose -f docker-compose.prod.yml logs --tail 50 caddy      # certificate issues
docker stats --no-stream                                            # memory vs the 1.2 GB limit
grep IMAGE_TAG .env                                                 # what is deployed
curl -fsS "https://$(grep ORYN_DOMAIN .env | cut -d= -f2)/health"
free -m; df -h /                                                    # swap and disk
# back up the SQLite memory volume
docker run --rm -v oryn_oryn-data:/data -v "$PWD":/out alpine tar czf /out/oryn-data-$(date +%F).tgz -C /data .
```

## Security notes

- Secrets exist only in `/opt/oryn/backend.env` (mode 600) and GitHub secrets. Logs carry no audio or transcripts.
- The backend container runs as a non-root user; only Caddy publishes ports.
- SSH is key-only. Consider restricting the deploy key in `authorized_keys` (`restrict,command=...`)
  or moving to SSM later.
- CI blocks on any `pip-audit` finding and on gitleaks hits.

## Not verified yet

The Docker image and compose stack have not been built or run by the author (no Docker on the dev
machine); the first real CI run and first EC2 deploy are the verification. See
`docs/features/F16-cicd-deployment.md`.
