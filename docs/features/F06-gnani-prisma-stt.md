# F06 — Gnani Prisma STT adapter

**Status:** IMPLEMENTED · **Branch:** `feat/F06-providers` · **Phase:** 2 · **Depends on:** F02 (contracts)

## Overview / problem
Turn the user's PCM stream into `SpeechStarted` and final `TranscriptEvent`s using Gnani Prisma's
realtime WebSocket, behind the `SpeechToText` port. Prisma needs custom upgrade headers (so the
backend must proxy) and exactly 1024-byte frames.

## Goals · Non-goals
- Goals: streaming STT adapter, exact framing, error/close-code mapping, no added latency.
- Non-goals: partial transcripts (Prisma has none), reconnect policy (caller decides from `retryable`),
  mid-session `update_settings`.

## User stories
- As a user I talk in Hindi/English/Hinglish and Oryn hears me; my interruption is detected quickly.

## Functional requirements
- Config in upgrade headers: `x-api-key-id`, `language_code`, `model`, `x-sample-rate`, `format`.
- Re-chunk arbitrary PCM into 1024-byte frames; `finish()` pads the remainder and sends `{"type":"commit"}`.
- `speech_start` -> `SpeechStarted`; `transcript` -> `TranscriptEvent(is_final=True)`; `error` -> `ProviderError`.

## Non-functional requirements (latency, limits)
- No sleeps or buffering beyond the <1024-byte remainder; the browser already paces at real time.
- Connect timeout 5 s. 15-minute session cap, 60 s idle timeout (server side).

## Design (classes, files, data flow)
`backend/app/providers/gnani_prisma.py`: `FrameChunker` (pure), `close_error()` (pure mapping),
`GnaniPrismaSTTStream` (owns one reader task + an event queue), `GnaniPrismaSTT` (factory, opens the socket).

## Data / API / WebSocket changes
None to the browser contract. Uses `Settings.gnani_*`.

## Security
API key only in the upgrade header from the backend; never logged. No audio or transcripts logged.

## Failure and edge cases
| Situation | Result |
| --- | --- |
| HTTP 401 on upgrade | `ProviderError(retryable=False)` from `open_stream` |
| HTTP 429/5xx, refused, timeout | `ProviderError(retryable=True)` |
| `error` message then close 1008 | non-retryable, message surfaced |
| close 1001 (15-min cap), 1006/other | retryable |
| `error` message with a normal close | non-retryable |
| clean close 1000 | `events()` ends without error |
| `send_audio` after the socket died | raises the same mapped `ProviderError` |
| `close()` twice | no-op; `events()` terminates |

## Acceptance criteria
Frames are always 1024 bytes; events map as above; errors carry correct `retryable`; close is idempotent.

## Test cases
`backend/tests/test_gnani_prisma.py` (15 tests, local `websockets.serve` fake server) and a
`@pytest.mark.live` smoke test in `test_providers_live.py`.

## Implementation notes (what actually got built, deviations)
- `ping_interval=None`: audio flows continuously and Prisma enforces its own idle timeout; the
  server's reaction to client pings is undocumented.
- The event queue is unbounded on purpose: one item per VAD segment, and it makes the `close()`
  sentinel safe. Deviation from the "bounded queues" standard.
- After an `error` message the reader waits up to 1 s for the close frame so the close code
  (1008 vs 1001) decides `retryable`.
- `events()` does not end after the final transcript; it ends when the socket closes or `close()` is called.
- Transcript `language` comes from `detected_language_code` (v3.0); `None` otherwise.

## Verification evidence
`pytest -q`: 58 passed, 2 skipped (live). `ruff check .`, `ruff format --check .`, `mypy app`: clean.
Live call against the real Prisma: NOT RUN (no keys in this environment).
