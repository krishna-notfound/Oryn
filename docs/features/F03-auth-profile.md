# F03 — Supabase auth + profile (backend part)

**Status:** IMPLEMENTED (backend; UI is on `feat/F01-web`) · **Branch:** `feat/F02-server` · **Phase:** 1 · **Depends on:** F02

## Overview / problem
Every socket and REST call must be tied to a verified Supabase user, and replies need that user's
preferences.

## Goals · Non-goals
- Goals: JWT verification (HS256 or JWKS), `current_user` dependency, profile loading into
  `UserContext`.
- Non-goals: sign-in UI, onboarding screens, profile writes (the browser writes via RLS).

## User stories
- As a user my identity comes from my token, never from a field I send.
- As a user my preferred name, language and style shape replies.

## Functional requirements
1. `SupabaseAuthenticator.verify(token) -> AuthenticatedUser(user_id, email)`.
2. HS256 if `supabase_jwt_secret` is set, else JWKS (`jwt.PyJWKClient`, cached 1 h, fetched in a
   thread). Require `aud=authenticated`, `exp`, `sub` (UUID) and `iss` (when the URL is known).
3. `AuthError` on any failure; no detail leaks.
4. `current_user` FastAPI dependency reads the Bearer header; `app.state.authenticator` holds the verifier.
5. `ProfileRepository.load(user_id) -> UserContext` via PostgREST with the service-role key.

## Non-functional requirements (latency, limits)
5 s total / 3 s connect timeout on PostgREST; JWKS cached so verification is local after warm-up.

## Design (classes, files, data flow)
`app/auth/supabase.py`, `app/auth/profiles.py`. `session.start.token` -> `verify` -> `load(user_id)`
-> `UserContext` passed to the conversation factory.

## Data / API / WebSocket changes
Reads `profiles` columns: preferred_name, primary_language, extra_languages, style, response_length,
listen_first, memory_enabled, timezone, interests.

## Security
Fixed algorithm lists (no `none`, no HS/RS confusion); UUID-only subjects; token never logged. See
`docs/context/security-and-safety.md`.

## Failure and edge cases
Missing profile row or unreachable database -> default `UserContext` (logged). Unknown
`response_length` -> `short`. Unconfigured auth (no secret, no URL) -> every token rejected.

## Acceptance criteria
Valid token accepted; expired, wrong audience, wrong issuer, bad signature, `alg=none`, garbage and
empty tokens rejected; JWKS path works with a locally generated RSA key.

## Test cases
`tests/test_auth.py` (HS256 matrix, JWKS valid/wrong-key/downgrade, REST dependency),
`tests/test_persistence.py` (profile request URL, headers and user filter, mapping, defaults, failure).

## Implementation notes (what actually got built, deviations)
- `UserContext` has no `save_history` field, so the session cannot gate persistence itself; whoever
  writes messages must read `profiles.save_history`.
- `PyJWKClient` refetches the key set on an unknown `kid`, so a forged `kid` can trigger JWKS fetches.
  Acceptable for now; add a cooldown if it shows up in logs.
- The JWKS path was tested with a stubbed client and a locally generated RSA key (no network); the
  real endpoint has not been exercised.

## Verification evidence
Run in `backend/`: `pytest -q` -> `48 passed`; `ruff check .` clean; `ruff format --check .` clean;
`mypy app` -> `Success: no issues found in 21 source files`.
