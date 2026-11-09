# F13 — Agent tools

**Status:** IMPLEMENTED · **Branch:** `feat/F12-memory-tools` · **Phase:** 3 · **Depends on:** F07 (engine calls `ToolRunner`), F12 (memory tools)

## Overview / problem
The model needs a few safe, bounded abilities (time, maths, weather, reading a page, memory) without
ever being able to reach beyond an explicit allowlist or into private networks.

## Goals · Non-goals
- Goals: a `ToolRegistry` implementing the `ToolRunner` port; six built-in tools.
- Non-goals: `web_search` (no verified provider or key; future work), the engine tool-calling loop (F07), per-tool consent UI.

## User stories
"What time is it in London?", "what is 18% of 2450?", "weather in Pune?", "what does this link say?",
"remember that I like chai", "what do you remember about my work?"

## Functional requirements
Tools: `get_current_datetime`, `calculator`, `get_weather`, `fetch_webpage`, `search_memory`, `save_memory`.
Registry: allowlist, JSON-schema `specs()`, validated args, per-tool timeout, per-user sliding-window rate
limit (default 20 calls per 60 s across all tools), all failures returned as `"Error: ..."` strings, results capped at 4000 chars.

## Non-functional requirements
Timeouts: datetime and calculator 2 s, memory 5 s, weather 12 s (two 5 s requests), fetch 15 s (8 s per hop).

## Design (classes, files, data flow)
- `backend/app/tools/registry.py` — `Tool[ArgsT]` (ABC), `ToolError`, `SlidingWindowRateLimiter`, `ToolRegistry`.
- `backend/app/tools/builtin/` — `clock.py`, `calculator.py`, `weather.py`, `webpage.py`, `memory_tools.py`; `build_default_tools(store, http_client)` in `__init__.py`.
- Call path: engine -> `ToolRegistry.run(call, user)` -> lookup -> rate limit -> validate -> `asyncio.timeout` -> `tool.run`.

## Data / API / WebSocket changes
None. Weather uses the Open-Meteo geocoding and forecast endpoints (no key).

## Security
- Calculator: whitelist AST walk (numbers, + - * / // % **, unary +/-), no names, calls or attributes; exponent <= 100, magnitude <= 1e100, length <= 200; complex, NaN and inf results rejected.
- `fetch_webpage` SSRF defence: http/https on ports 80/443 only, no credentials in the URL; the host is resolved and every address must be globally routable (blocks loopback, private, link-local including 169.254.169.254, CGNAT, multicast, reserved, IPv4-mapped IPv6); the connection is pinned to the validated IP (Host and SNI preserved) to stop DNS rebinding; redirects are followed manually (max 3) with full re-validation; 200 KB body cap; 2000-char text; output wrapped in `[UNTRUSTED WEB CONTENT ...]` markers, and the end marker is stripped from page text so a page cannot forge it.
- Memory tools respect `user.memory_enabled` and are scoped to the verified `user.user_id`.
- Logs contain tool name, duration and outcome only; error strings never echo argument values.

## Failure and edge cases
Unknown tool, bad args, timeout, rate limit, upstream outage, non-text content, HTTP errors, DNS failure: each returns a short error string. `CancelledError` still propagates.

## Acceptance criteria
- Anything unregistered is refused. Private, loopback, metadata and file URLs never produce a request.
- A tool that hangs is cut off at its timeout. Excess calls from one user do not affect others.

## Test cases
`tests/test_tool_registry.py` (14), `tests/test_builtin_tools.py` (39), `tests/test_fetch_webpage.py` (44).
Registry behaviours; calculator safe and unsafe table; weather and time happy and failure paths (`httpx.MockTransport`, no network);
memory tools consent gating; 22 blocked URL shapes; private-resolving hostnames; redirect to an internal address; DNS pinning
(request goes to the IP with the original Host and SNI); IPv6 literal; body and text caps; the untrusted wrapper cannot be forged.

## Implementation notes (what actually got built, deviations)
- `Tool` uses PEP 695 generics; `ToolRegistry(tools, rate_limiter=None)`.
- `fetch_webpage` takes an injectable `resolver` so tests never touch DNS.
- The rate limit is per user across all tools, not per tool.
- Future work: `web_search` once a provider and key are verified.

## Verification evidence
Run in `backend/` on 2026-11-09: `pytest -q` -> `154 passed`; `ruff check .` -> `All checks passed!`; `ruff format --check .` -> `28 files already formatted`; `mypy app` -> `Success: no issues found in 21 source files`.

Manual live smoke (real network, throwaway script, not part of the suite): `get_weather Pune` returned a real forecast;
`fetch_webpage https://example.com` returned wrapped text through the IP-pinned TLS path; `http://localhost:8000` returned `Error: that URL is not allowed`.

Not verified: behaviour inside the real engine tool loop; IP-pinned TLS against sites other than example.com.
