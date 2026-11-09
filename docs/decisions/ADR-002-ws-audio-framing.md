# ADR-002 — One WebSocket per conversation, generation-tagged binary audio

**Status:** Accepted · 2026-11-09

**Decision.** A single persistent socket carries JSON control messages and binary PCM both ways.
Server audio frames are prefixed with a 4-byte big-endian `generation_id`. Auth is the first
message (`session.start` with the Supabase token), not a URL query.

**Why.** No reconnect per turn (latency); binary avoids base64 overhead (~33 %); the generation tag
makes barge-in race-free — the client discards stale frames without coordination; tokens stay out
of access logs.

**Rejected.** WebRTC (needs TURN/SFU infra — overkill for MVP); base64 JSON audio (bandwidth/CPU);
token in query string (leaks into logs).
