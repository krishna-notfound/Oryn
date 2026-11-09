# F05 — Realtime WebSocket session

**Status:** IMPLEMENTED · **Branch:** `feat/F02-server` · **Phase:** 1 · **Depends on:** F02, F03

## Overview / problem
One socket per conversation must authenticate, route frames to a `Conversation`, send engine output
back safely, and enforce limits, all without knowing any provider.

## Goals · Non-goals
- Goals: handshake, routing of every client message, safe sink, rate/size limits, single session per
  user, guaranteed cleanup.
- Non-goals: the engine behind `Conversation`, reconnection logic (client), message persistence.

## User stories
- As a user I connect, am authenticated by my first message and hear replies on the same socket.
- As the operator a second tab cleanly supersedes the first.

## Functional requirements
1. Accept; `session.start` within `session_handshake_timeout_s` else close `4408`.
2. Verify token (`4401`), load profile, resolve/create conversation, build the `Conversation` via
   `ConversationFactory(user, start, sink, conversation_id)`, send `session.ready`, call `start()`.
3. Binary frames: size <= `MAX_AUDIO_FRAME_BYTES` and non-empty, token-bucket rate limit (`4429`),
   then `Conversation.on_audio`.
4. Text frames: `parse_client_message` then dispatch (`user.text`, `user.interrupt`, `audio.end`,
   `playback.started/finished`, `ping` -> `pong`, `session.end` -> clean close).
5. Malformed input -> `error{bad_message}`; third strike closes `4400`.
6. Session cap `session_max_duration_s` -> `error{session_limit}` and clean close.
7. One active session per user; a new one closes the old with `4409`.
8. `Conversation.close()` always runs.

## Non-functional requirements (latency, limits)
Audio frame <= 32 KB; ~100 frames/s steady, burst 100 (nominal capture is ~50/s); text frame <= 64 KB;
writes serialised by an `asyncio.Lock`.

## Design (classes, files, data flow)
`app/websocket/session.py`: `VoiceSession`, `WebSocketEventSink`, `SessionRegistry`, `SessionServices`,
`CloseCode`, `ConversationFactory`. `app/websocket/ratelimit.py`: `TokenBucket`. Eviction cancels the
old session task (it may be blocked in `receive()`); its `finally` then runs the normal shutdown.

## Data / API / WebSocket changes
Implements `docs/context/websocket-contract.md`. The handshake timeout closes `4408` (the contract
prose says `4401`, its close-code table says `4408`; the table was followed).

## Security
Auth-first message, no token in URL, generic errors, ids/durations only in logs, no transcripts.

## Failure and edge cases
Client disconnect mid-turn; send after close (dropped); persistence outage (conversation id falls back
to an unpersisted uuid); unexpected exception (`error{internal}`, close `1011`); eviction while starting.

## Acceptance criteria
Each close code in the contract (`4400`, `4401`, `4408`, `4409`, `4429`) is exercised by a test.

## Test cases
`tests/test_session.py`: handshake timeout, bad token, non-start first frame, identity from token,
foreign vs own conversation id, routing of every message type, session.end, disconnect cleanup,
oversize frame, three strikes, frame flood, supersede, independent users, duration cap, `/health`,
sink drops after failure.

## Implementation notes (what actually got built, deviations)
- Rate limiting is one bucket over all frames (audio and text); `FRAME_BURST` and `FRAMES_PER_SECOND`
  live in `session.py` because `config.py` is a frozen contract.
- An oversized or empty audio frame counts as a strike rather than closing immediately.
- The session does not write messages. The factory closure given to the engine should capture a
  `ConversationRepository` and honour `profiles.save_history`.
- `conversations.ended_at` is never set yet.

## Verification evidence
Run in `backend/`: `pytest -q` -> `48 passed` (three consecutive runs, no flakes);
`ruff check .` -> `All checks passed!`; `ruff format --check .` -> `25 files already formatted`;
`mypy app` -> `Success: no issues found in 21 source files`.
