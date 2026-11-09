# ADR-001 — Gnani Prisma + Gnani Timbre for voice, Sarvam for the LLM

**Status:** Accepted · 2026-11-09

**Context.** Oryn is built for the Gnani AI Internship Challenge; Gnani speech must be meaningfully
integrated. Gnani offers no hosted LLM API (Evon is open-weights only).

**Decision.** Default STT = Gnani Prisma streaming WS (`prisma-v3.0`, `codemix` for Hinglish).
Default TTS = Gnani Timbre streaming WS (`timbre-v2.5`, raw PCM 24 kHz). LLM = Sarvam
conversational model through an adapter implementing `LanguageModel`. All behind
`providers/base.py`; selection in `providers/registry.py`.

**Consequences.** Browser cannot set Prisma's upgrade headers → backend must proxy audio (it
does anyway). Prisma gives no partials → the UI shows "listening…" until the final transcript;
`speech_start` doubles as a server-side barge-in signal. Swapping vendors touches one adapter.
