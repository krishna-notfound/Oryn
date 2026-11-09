# Testing

Rule zero: **never claim a test passed, or a feature works, unless it was run and the output seen.**
`VERIFIED` in a feature spec needs evidence (command output, recording, metrics file).

## Pyramid

| Layer | What | Where | Runs |
| ----- | ---- | ----- | ---- |
| Unit | Pure logic and single classes (state machine, segmenter, context builder, memory store, tools, safety rules) driven by the fakes in `backend/app/providers/fakes.py`. One behaviour per test, no mocks of our own code. | `backend/tests/` | every CI run |
| WebSocket integration | Real app + FastAPI `TestClient.websocket_connect("/ws")` with `PROVIDER_MODE=fake`: handshake/auth, a full turn, barge-in (new speech during TTS bumps the generation id and stale audio is dropped), session limits, malformed messages. | `backend/tests/` | every CI run |
| Live provider | Real Gnani Prisma / Timbre and Sarvam calls. Marked `@pytest.mark.live`; need real keys; cost money. | `backend/tests/` | manually only |
| Frontend | `npm run lint`, `npx tsc --noEmit`, `npm run build` (CI, once `apps/web` exists). Audio worklet and playback queue logic live in `lib/audio/` and `lib/realtime/` as plain classes so they can be unit-tested. | `apps/web/` | CI |
| Voice evaluation | End-to-end on deployed infra with a real microphone; checklist below. | manual | before demo / release |

CI command (identical locally): `pytest -q -m "not live"`. Register the marker in
`pyproject.toml` (`markers = ["live: calls real providers; run manually"]`) when the first live test lands.

Run live tests by hand:
```bash
cd backend && pytest -q -m live          # needs GNANI_API_KEY, SARVAM_API_KEY in backend/.env
```

## Static checks (backend, all blocking in CI)

`ruff check .`, `ruff format --check .`, `mypy app` (strict), `pip-audit --skip-editable`,
and a gitleaks secret scan on the commits of the change. The Docker image is built (not pushed) on every PR.

## Voice evaluation checklist

Run against the deployed stack (`PROVIDER_MODE=live`), headphones on, quiet room, Chrome.
Record the date, commit sha and results in the feature spec's *Verification evidence*.

1. **Round trip**: speak a short English, Hindi and Hinglish sentence; each gets a spoken reply in the same language.
2. **Latency**: hold 20 turns of 3-8 s utterances. From the per-turn `metrics` events (see
   `backend/app/voice/latency.py`) report **p50 and p95** for `stt_ms`, LLM first token,
   TTS first byte (field names as emitted) and the headline *speech end -> first audio*. Targets (not promises): p50
   500-700 ms. Report what was measured, including misses; do not round down.
3. **Barge-in**: while the assistant is mid-sentence, start talking. Expect playback to stop within
   ~100 ms, no leftover audio from the old reply, and the next reply to answer the new utterance.
   Repeat 10 times, including a barge-in during the first second and one right at a sentence boundary.
4. **Silence / noise**: 10 s of silence and keyboard noise must not trigger a reply.
5. **Long session**: a 10+ minute conversation stays stable (no growing latency, memory use under 1.2 GB via `docker stats`).
6. **Reconnect**: kill the network for 5 s; the UI reports it and a new session starts cleanly.
7. **Auth**: a WebSocket without a valid Supabase JWT is rejected.
8. **Memory/consent**: nothing is remembered without consent; with consent a fact from session 1 is recalled in session 2.
9. **Safety**: a crisis phrase gets the safe-response path, not a normal reply.

## Writing tests

- Descriptive names (`test_barge_in_drops_audio_from_previous_generation`).
- Fakes over mocks; time and queues injected, no `sleep` for synchronisation.
- No secrets, audio fixtures of real people, or transcripts in the repo.
