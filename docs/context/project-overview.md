# Oryn — project overview

**Oryn** (formerly *Saathi*): a realtime, voice-first, multilingual AI companion that feels like
talking to someone present and attentive. Built for the **Gnani AI Internship Challenge**.

## Why it is good (quality drivers, in order)

1. Low perceived latency (speech end → first audio 500–700 ms).
2. Natural turn-taking and instant barge-in.
3. Reliable Hindi / English / Hinglish recognition (Gnani Prisma).
4. Natural voice (Gnani Timbre).
5. Contextual, consented memory; consistent personality.
6. A simple, calm UI: open → friendly orb → *Start Talking*.

## MVP scope

Auth · light onboarding · voice conversation · text fallback · streaming STT/TTS · barge-in ·
conversation context · user-controlled memory · small tool set · history · profile/settings ·
error recovery · latency monitoring.

## Non-goals

Analytics dashboards, admin panels, avatar customisation, social/feeds, gamification, emotion
scoring, therapy/diagnosis, multi-agent systems, microservices, self-hosted LLMs, native apps.

## Personality

Friendly, natural, attentive, concise by default, non-judgmental. Never pretends to be human,
never diagnoses, avoids robotic filler. Asks before advising when the user wants to be heard.
Mirrors the user's language (Hindi / English / Hinglish).

## Safety

Supportive companion, not a therapist. On self-harm / danger signals: respond with care,
encourage reaching a trusted person / professional help; in India offer **Tele-MANAS 14416**.

## Challenge demo script (must work end to end)

1. User speaks naturally (Hinglish).
2. **Gnani Prisma** transcribes (shown live as `transcript.final`).
3. Oryn uses conversation context (+ memory if enabled).
4. **Sarvam LLM** streams a reply.
5. **Gnani Timbre** synthesises it sentence by sentence.
6. Browser plays the first sentence while the rest is still generating.
7. User interrupts mid-sentence → audio stops instantly → Oryn listens.
8. Conversation continues naturally.
9. Latency panel shows STT / LLM / TTS / end-to-end p50 & p95.

## Success criteria

The demo above runs on deployed infra; tests are green in CI; latency is measured, not claimed.
