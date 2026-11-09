"""get_current_datetime: the model has no clock, so it asks for one."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.conversation.ports import UserContext
from app.tools.registry import Tool, ToolError


class DateTimeArgs(BaseModel):
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA zone such as 'Europe/London'; default: the user's.",
    )


class CurrentDateTimeTool(Tool[DateTimeArgs]):
    name = "get_current_datetime"
    description = "Get the current date and time, in the user's timezone unless another is given."
    args_model = DateTimeArgs
    timeout_s = 2.0

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now

    async def run(self, args: DateTimeArgs, user: UserContext) -> str:
        zone_name = args.timezone or user.timezone
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ToolError(f"unknown timezone '{zone_name[:64]}'") from None
        local = self._now().astimezone(zone)
        return f"{local:%A, %d %B %Y, %I:%M %p} ({zone_name})"
