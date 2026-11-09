# Features

Status flow: `DRAFT → UNDER_REVIEW → APPROVED → IN_PROGRESS → IMPLEMENTED → VERIFIED` (or `BLOCKED`).
`VERIFIED` only with evidence (test output, demo recording, metrics). One spec per feature,
written from `_TEMPLATE.md`. Update this table whenever a status changes.

| ID  | Feature                                   | Status      | Branch / worktree            |
| --- | ----------------------------------------- | ----------- | ---------------------------- |
| F01 | Frontend foundation + design system       | IN_PROGRESS | `feat/F01-web`               |
| F02 | Backend foundation + config               | IN_PROGRESS | `feat/F02-server`            |
| F03 | Supabase auth + profile                   | IN_PROGRESS | `feat/F02-server` (backend), `feat/F01-web` (UI) |
| F04 | Onboarding + preferences                  | IN_PROGRESS | `feat/F01-web`               |
| F05 | Realtime WebSocket session                | IN_PROGRESS | `feat/F02-server`            |
| F06 | Gnani Prisma STT adapter                  | IN_PROGRESS | `feat/F06-providers`         |
| F07 | Sarvam LLM adapter + conversation engine  | IN_PROGRESS | `feat/F06-providers` (adapter), `feat/F07-engine` (engine) |
| F08 | Gnani Timbre TTS + browser playback       | IN_PROGRESS | `feat/F06-providers`, `feat/F01-web` |
| F09 | Voice state machine + barge-in            | IN_PROGRESS | `feat/F07-engine`            |
| F10 | Text chat fallback                        | IN_PROGRESS | `feat/F01-web`               |
| F11 | Conversation history + context            | APPROVED    | `feat/F07-engine` (context), later: persistence |
| F12 | Persistent memory + consent               | IN_PROGRESS | `feat/F12-memory-tools`      |
| F13 | Agent tools                               | IN_PROGRESS | `feat/F12-memory-tools`      |
| F14 | Safety                                    | IN_PROGRESS | `feat/F07-engine`            |
| F15 | Observability + latency metrics           | IN_PROGRESS | `feat/F07-engine`            |
| F16 | CI/CD + Docker + deployment               | IN_PROGRESS | `feat/F16-devops`            |
