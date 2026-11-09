# WebSocket contract

Source of truth in code: `backend/app/websocket/protocol.py` (Pydantic). Frontend mirrors it in
`apps/web/lib/realtime/protocol.ts`. Change both together.

Endpoint: `wss://<backend>/ws` (one socket per conversation, reused for every turn).

## Handshake

1. Browser opens the socket (no token in the URL — keeps it out of logs).
2. First text frame **must** be `session.start` with the Supabase access token, within 5 s,
   otherwise close `4401`.
3. Server verifies the JWT, loads the profile, replies `session.ready`.

## Frames

| Direction | Kind   | Content |
| --------- | ------ | ------- |
| C → S     | binary | PCM s16le mono @ `input_sample_rate` (16 000). ≤ 32 KB per frame. |
| S → C     | binary | `uint32 BE generation_id` + PCM s16le @ `assistant.audio.start.sample_rate` |

### Client → server JSON

| type               | fields                                  | meaning |
| ------------------ | --------------------------------------- | ------- |
| `session.start`    | token, language, mode, conversation_id? | authenticate + open |
| `user.text`        | text (≤ 2000)                           | typed message |
| `user.interrupt`   | —                                       | user started talking over Oryn |
| `audio.end`        | —                                       | mic stopped; flush STT |
| `playback.started` | generation_id                           | first sample audible (latency mark) |
| `playback.finished`| generation_id                           | queue drained |
| `session.end`      | —                                       | close cleanly |
| `ping`             | —                                       | keepalive (every 20 s) |

### Server → client JSON

| type                   | fields |
| ---------------------- | ------ |
| `session.ready`        | session_id, conversation_id, input_sample_rate, output_sample_rate |
| `state`                | state (VoiceState) |
| `transcript.partial`   | text (only if provider supports partials — Prisma does not) |
| `transcript.final`     | turn_id, text, language |
| `assistant.text`       | generation_id, delta |
| `assistant.audio.start`| generation_id, sample_rate, encoding |
| `assistant.done`       | generation_id, text |
| `interrupted`          | generation_id |
| `metrics`              | turn_id, generation_id, stt_ms, llm_ttft_ms, tts_ttfb_ms, speech_end_to_first_audio_ms, interrupt_to_stop_ms |
| `error`                | code, message, retryable |
| `pong`                 | — |

## Close codes

`4400` bad message · `4401` unauthenticated · `4408` handshake timeout · `4429` rate limited ·
`1011` internal error. Client reconnects with backoff (0.5 s, 1 s, 2 s, max 5 tries) except on 4401.

## Limits

Max 1 active socket per user (new one closes the old with `4409`), 32 KB audio frames,
~ 50 audio frames/s, 2000-char text, 15-minute session cap (matches Prisma).
