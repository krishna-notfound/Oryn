import asyncio

import pytest
from pydantic import BaseModel, Field

from app.conversation.ports import UserContext
from app.providers.base import ToolCall
from app.tools.registry import SlidingWindowRateLimiter, Tool, ToolError, ToolRegistry

USER = UserContext(user_id="alice")


class NoArgs(BaseModel):
    pass


class EchoArgs(BaseModel):
    text: str = Field(max_length=20, description="Text to echo")
    times: int = Field(default=1, ge=1, le=3)


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "Echo text back"
    args_model = EchoArgs

    async def run(self, args: EchoArgs, user: UserContext) -> str:
        return " ".join([args.text] * args.times)


class SlowTool(Tool[NoArgs]):
    name = "slow"
    description = "Never finishes in time"
    args_model = NoArgs
    timeout_s = 0.05

    async def run(self, args: NoArgs, user: UserContext) -> str:
        await asyncio.sleep(5)
        return "too late"


class FailingTool(Tool[NoArgs]):
    name = "failing"
    description = "Fails on demand"
    args_model = NoArgs

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def run(self, args: NoArgs, user: UserContext) -> str:
        raise self._error


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


async def test_runs_registered_tool_with_validated_args() -> None:
    registry = ToolRegistry([EchoTool()])
    assert await registry.run(call("echo", text="hi", times=2), USER) == "hi hi"


async def test_unknown_tool_is_rejected_without_raising() -> None:
    registry = ToolRegistry([EchoTool()])
    result = await registry.run(call("rm_rf", path="/"), USER)
    assert result.startswith("Error:") and "does not exist" in result


@pytest.mark.parametrize(
    "arguments", [{}, {"text": 5}, {"text": "x" * 21}, {"text": "ok", "times": 99}]
)
async def test_bad_arguments_become_an_error_string(arguments: dict[str, object]) -> None:
    result = await ToolRegistry([EchoTool()]).run(call("echo", **arguments), USER)
    assert result.startswith("Error: invalid arguments for echo")


async def test_error_does_not_echo_argument_values() -> None:
    secret = "hunter2-" * 5
    result = await ToolRegistry([EchoTool()]).run(call("echo", text=secret), USER)
    assert "hunter2" not in result


async def test_timeout_is_enforced() -> None:
    result = await ToolRegistry([SlowTool()]).run(call("slow"), USER)
    assert result == "Error: slow timed out."


async def test_tool_errors_are_shown_but_crashes_are_generic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ToolRegistry(
        [FailingTool(ToolError("city not found"))],
    )
    assert await registry.run(call("failing"), USER) == "Error: city not found"

    crashing = ToolRegistry([FailingTool(RuntimeError("secret internals /etc/passwd"))])
    result = await crashing.run(call("failing"), USER)
    assert result == "Error: failing failed unexpectedly."
    assert "secret internals" not in caplog.text


async def test_logs_contain_name_outcome_and_duration_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    await ToolRegistry([EchoTool()]).run(call("echo", text="private words"), USER)
    (record,) = [r for r in caplog.records if r.message == "tool call"]
    assert (record.tool, record.outcome) == ("echo", "ok")  # type: ignore[attr-defined]
    assert "private words" not in caplog.text


async def test_rate_limit_is_per_user_and_slides() -> None:
    clock = Clock()
    registry = ToolRegistry([EchoTool()], SlidingWindowRateLimiter(2, 10.0, clock))
    bob = UserContext(user_id="bob")

    assert await registry.run(call("echo", text="a"), USER) == "a"
    assert await registry.run(call("echo", text="a"), USER) == "a"
    limited = await registry.run(call("echo", text="a"), USER)
    assert "too many tool calls" in limited
    assert await registry.run(call("echo", text="b"), bob) == "b"  # other users unaffected

    clock.now = 10.0
    assert await registry.run(call("echo", text="a"), USER) == "a"


async def test_result_is_capped() -> None:
    class Flood(Tool[NoArgs]):
        name = "flood"
        description = "Returns far too much"
        args_model = NoArgs

        async def run(self, args: NoArgs, user: UserContext) -> str:
            return "x" * 100_000

    assert len(await ToolRegistry([Flood()]).run(call("flood"), USER)) == 4000


def test_specs_expose_json_schema_for_each_tool() -> None:
    (spec,) = ToolRegistry([EchoTool()]).specs()
    assert (spec.name, spec.description) == ("echo", "Echo text back")
    assert spec.parameters["properties"]["text"]["maxLength"] == 20
    assert spec.parameters["required"] == ["text"]


def test_duplicate_tool_names_are_refused() -> None:
    with pytest.raises(ValueError):
        ToolRegistry([EchoTool(), EchoTool()])
