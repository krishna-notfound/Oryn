# Security and safety

Code: `backend/app/auth/`, `backend/app/websocket/session.py`, `supabase/migrations/`.

## Identity

- **JWT verification** (`auth/supabase.py`): HS256 when `SUPABASE_JWT_SECRET` is set, otherwise
  asymmetric (RS256/ES256) against the project's JWKS (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`,
  cached 1 h, fetched off the event loop). The algorithm list is fixed per mode, so `alg: none` and
  HS/RS downgrade tokens fail. Required claims: `exp`, `sub`, `aud == "authenticated"`, and `iss`
  (`{SUPABASE_URL}/auth/v1`) whenever the project URL is known. `sub` must be a UUID.
- Every failure raises a bare `AuthError`; the client only sees `4401` / `401`. The reason class
  (e.g. `ExpiredSignatureError`) is logged, never the token.
- The user id always comes from the verified token. Ids sent by the browser (including
  `conversation_id`) are never trusted: a supplied conversation is reused only if
  `conversations.user_id` matches, otherwise a new one is created.

## Database and RLS

- RLS is on for `profiles`, `conversations`, `messages`, `consents`; policies are keyed on
  `(select auth.uid())`. The browser (anon key) can only touch its own rows.
- `messages` has no insert/update policy for browser roles: only the backend writes them, and only
  when `profiles.save_history` is true. `consents` is append-only for the browser.
- **Service-role rule:** the service-role key bypasses RLS. It lives only in backend env
  (`SUPABASE_SERVICE_ROLE_KEY`), is used only by `PostgrestClient`, only after JWT verification, and
  every query filters by the verified `user_id`. Never ship it to the browser, logs or git.

## WebSocket

- **Auth-first message:** no token in the URL. The first text frame must be `session.start` within
  `SESSION_HANDSHAKE_TIMEOUT_S` (5 s) or the socket closes `4408`. Bad token `4401`, malformed first
  frame `4400`.
- Limits: audio frame <= 32 KB, text frame <= 64 KB, `user.text` <= 2000 chars, a token bucket of
  ~100 frames/s (burst 100) per socket (`4429` on abuse), 15-minute session cap, one live socket per
  user (a newer one closes the older with `4409`).
- Three malformed messages on one socket close it with `4400`. Server errors sent to clients are
  generic; stack traces and provider details stay in server logs.
- No cookies are used, so cross-site WebSocket hijacking has nothing to ride on; the token must be
  presented explicitly.

## CORS

`CORS_ORIGINS` is an explicit allow-list (no `*`), no credentials, methods `GET POST PATCH DELETE`,
headers `Authorization` and `Content-Type`.

## Secrets and logging

- Secrets are env vars read only in `app/config.py` and held as `SecretStr`; `.env` is git-ignored.
- Logs carry ids, close codes and durations. **Never** transcripts, audio, tokens or profile
  content. Pydantic validation errors (which echo input) are not logged.
- Raw audio is never stored.

## Data deletion

Deleting an `auth.users` row cascades to `profiles`, `conversations`, `messages` and `consents`.
Users can delete conversations and messages directly (RLS allows it). SQLite memories are deleted
by `user_id` through the memory REST API (see `data-and-memory.md`); account deletion must also
purge that user's SQLite rows.

## Prompt injection and tools

Tool output and retrieved memories are untrusted data, never instructions. Tools are allow-listed,
max 3 calls per turn, take validated arguments and are scoped to the verified user. Rules live
with the tool registry (F13) and the prompt builder (F07); the session layer never executes
model-chosen actions.

## Safety

Oryn is a companion, not a clinician. On signs of self-harm or crisis the reply must be warm,
non-judgmental, encourage reaching a trusted person or professional, and in India offer
**Tele-MANAS 14416** (24x7, free). Crisis detection and prompt rules: F14 (`app/safety/`). Do not
diagnose, prescribe, or promise confidentiality the product cannot keep.
