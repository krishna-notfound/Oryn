# Code standards

The bar: a senior reviewer reads any file top-to-bottom and understands it without asking.

## Everywhere

- **ponytail**: simplest code that works. Stdlib before a dependency. No speculative
  abstraction, no "manager/helper/util" grab-bags, no dead options.
- One responsibility per module and per class. Name files after the responsibility.
- Fix root causes, not symptoms. Errors are handled where they can be handled, otherwise raised.
- Comments explain *why*, never *what*. Public seams get a one-paragraph docstring.

## Python (backend)

- Python 3.12, fully typed (`mypy --strict` clean), `ruff` clean, `ruff format`.
- OOP where state lives (sessions, streams, state machine, stores); plain functions for pure logic.
- `@dataclass(frozen=True, slots=True)` for value objects; Pydantic v2 only at I/O boundaries
  (WS messages, settings, tool args).
- Async all the way: `httpx.AsyncClient`, `websockets`, `asyncio.TaskGroup`; never block the loop
  (SQLite work goes through `asyncio.to_thread`).
- Every background task is owned: created by an object that cancels and awaits it in `close()`.
- Bounded queues (`asyncio.Queue(maxsize=…)`), explicit timeouts on every network call.
- Cancellation-safe: catch `CancelledError` only to clean up, then re-raise.
- Logging: `logging.getLogger(__name__)`, structured `extra={...}`; no transcripts/audio in logs.
- Settings only via `app/config.py` (`pydantic-settings`); no `os.environ` elsewhere.
- Tests: pytest + pytest-asyncio, fakes over mocks, one behaviour per test, descriptive names.

## TypeScript (frontend)

- Strict mode, no `any`. Server Components by default; `"use client"` only for interactivity.
- Realtime/audio logic lives in `lib/realtime/` and `lib/audio/` as small classes, not in components.
- Tailwind with tokens from the Stitch design (`design/stitch/warm_tactile_voice/DESIGN.md`).
- Lucide icons. shadcn/ui only where it genuinely saves work.

## Git

- Conventional commits (`feat(voice): …`, `fix(ws): …`). Small commits, each green.
- Branch `feat/<ID>-<slug>`; PR to `main`; CI must pass; squash merge.
