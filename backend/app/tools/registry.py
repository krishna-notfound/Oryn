"""Tool registry: the only way the model can make anything happen.

Safety properties, all enforced here rather than trusted to individual tools:
- explicit allowlist (a call to anything unregistered is refused),
- arguments validated against a Pydantic model before the tool sees them,
- hard per-tool timeout and a per-user sliding-window rate limit,
- failures become short error strings for the model, never exceptions,
- logs carry tool name, duration and outcome only - never arguments or results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.conversation.ports import ToolRunner, UserContext
from app.providers.base import ToolCall, ToolSpec

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 4000


class ToolError(Exception):
    """A failure whose message is safe and useful to show the model."""


class Tool[ArgsT: BaseModel](ABC):
    """One capability. Subclasses set the class attributes and implement `run`."""

    name: str
    description: str
    args_model: type[ArgsT]
    timeout_s: float = 10.0

    @abstractmethod
    async def run(self, args: ArgsT, user: UserContext) -> str:
        """Return a short plain-text result; raise `ToolError` for expected failures."""


class SlidingWindowRateLimiter:
    """Allows at most `max_calls` per `window_s` seconds for each key."""

    def __init__(
        self,
        max_calls: int = 20,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_calls = max_calls
        self._window_s = window_s
        self._clock = clock
        self._calls: defaultdict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        calls = self._calls[key]
        while calls and now - calls[0] >= self._window_s:
            calls.popleft()
        if len(calls) >= self._max_calls:
            return False
        calls.append(now)
        return True


class ToolRegistry(ToolRunner):
    def __init__(
        self, tools: Iterable[Tool[Any]], rate_limiter: SlidingWindowRateLimiter | None = None
    ) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter()

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(t.name, t.description, t.args_model.model_json_schema())
            for t in self._tools.values()
        ]

    async def run(self, call: ToolCall, user: UserContext) -> str:
        started = time.monotonic()
        outcome, text = await self._execute(call, user)
        logger.info(
            "tool call",
            extra={
                "tool": call.name[:64],
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return text[:MAX_RESULT_CHARS]

    async def _execute(self, call: ToolCall, user: UserContext) -> tuple[str, str]:
        tool = self._tools.get(call.name)
        if tool is None:
            return "unknown_tool", "Error: that tool does not exist."
        if not self._rate_limiter.allow(user.user_id):
            return "rate_limited", "Error: too many tool calls right now; try again shortly."
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            )
            return "invalid_args", f"Error: invalid arguments for {tool.name} ({problems})."
        try:
            async with asyncio.timeout(tool.timeout_s):
                return "ok", await tool.run(args, user)
        except ToolError as exc:
            return "tool_error", f"Error: {exc}"
        except TimeoutError:
            return "timeout", f"Error: {tool.name} timed out."
        except Exception as exc:
            # No traceback: exception text may echo user-supplied arguments.
            logger.warning("tool crashed", extra={"tool": tool.name, "error": type(exc).__name__})
            return "error", f"Error: {tool.name} failed unexpectedly."
