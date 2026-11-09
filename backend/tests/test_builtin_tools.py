from datetime import UTC, datetime

import httpx
import pytest

from app.conversation.ports import UserContext
from app.memory.store import SQLiteMemoryStore
from app.providers.fakes import FakeEmbedder
from app.tools.builtin import build_default_tools
from app.tools.builtin.calculator import CalculatorArgs, CalculatorTool, evaluate
from app.tools.builtin.clock import CurrentDateTimeTool, DateTimeArgs
from app.tools.builtin.memory_tools import (
    SaveMemoryArgs,
    SaveMemoryTool,
    SearchMemoryArgs,
    SearchMemoryTool,
)
from app.tools.builtin.weather import WeatherArgs, WeatherTool
from app.tools.registry import ToolError, ToolRegistry

USER = UserContext(user_id="alice", memory_enabled=True, timezone="Asia/Kolkata")
MEMORY_OFF = UserContext(user_id="alice", memory_enabled=False)


# ------------------------------------------------------------------- datetime


async def test_datetime_uses_the_users_timezone() -> None:
    fixed = lambda: datetime(2026, 11, 9, 9, 0, tzinfo=UTC)  # noqa: E731
    tool = CurrentDateTimeTool(now=fixed)
    assert (
        await tool.run(DateTimeArgs(), USER) == "Monday, 09 November 2026, 02:30 PM (Asia/Kolkata)"
    )


async def test_datetime_accepts_explicit_timezone_and_rejects_unknown() -> None:
    tool = CurrentDateTimeTool(now=lambda: datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert "(Europe/London)" in await tool.run(DateTimeArgs(timezone="Europe/London"), USER)
    with pytest.raises(ToolError, match="unknown timezone"):
        await tool.run(DateTimeArgs(timezone="Mars/Olympus"), USER)


# ----------------------------------------------------------------- calculator


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("-4 + +2", -2),
        ("7 // 2", 3),
        ("7 % 4", 3),
        ("2 ** 10", 1024),
        ("2 ** -1", 0.5),
        ("10 / 4", 2.5),
        ("-(2 ** 2)", -4),
    ],
)
def test_calculator_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd')",
        "().__class__",
        "[1, 2]",
        "'a' * 3",
        "x + 1",
        "1 if True else 2",
        "lambda: 1",
        "True + 1",
        "1 < 2",
        "1 +",
        "",
        "2 ** 1000",
        "9 ** 9 ** 9",
        "10 ** 100 * 10",
        "1e999",
        "(-8) ** 0.5",
        "1 / 0",
        "1 // 0",
        "5 % 0",
        "1" * 201,
        "(" * 150 + "1" + ")" * 150,
    ],
)
def test_calculator_rejects_unsafe_or_invalid_input(expression: str) -> None:
    with pytest.raises(ToolError):
        evaluate(expression)


async def test_calculator_tool_formats_results() -> None:
    tool = CalculatorTool()
    assert await tool.run(CalculatorArgs(expression="10 / 4"), USER) == "10 / 4 = 2.5"
    assert await tool.run(CalculatorArgs(expression="0.1 + 0.2"), USER) == "0.1 + 0.2 = 0.3"


# -------------------------------------------------------------------- weather


def weather_transport(requests: list[httpx.Request], *, found: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            if not found:
                return httpx.Response(200, json={"generationtime_ms": 0.1})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"name": "Pune", "country": "India", "latitude": 18.5, "longitude": 73.8}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 28.4,
                    "apparent_temperature": 30.2,
                    "relative_humidity_2m": 61,
                    "weather_code": 2,
                    "wind_speed_10m": 11.6,
                },
                "daily": {
                    "temperature_2m_max": [31.0],
                    "temperature_2m_min": [22.0],
                    "precipitation_probability_max": [20],
                },
            },
        )

    return httpx.MockTransport(handler)


async def test_weather_happy_path_geocodes_then_fetches_forecast() -> None:
    requests: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=weather_transport(requests)) as client:
        text = await WeatherTool(client).run(WeatherArgs(location="Pune"), USER)
    assert text == (
        "Weather in Pune, India: 28°C (feels like 30°C), partly cloudy, humidity 61%, "
        "wind 12 km/h. Today: 22-31°C, chance of rain 20%."
    )
    assert requests[0].url.params["name"] == "Pune"
    assert requests[1].url.params["latitude"] == "18.5"


async def test_weather_unknown_place_and_outage_are_tool_errors() -> None:
    async with httpx.AsyncClient(transport=weather_transport([], found=False)) as client:
        with pytest.raises(ToolError, match="could not find"):
            await WeatherTool(client).run(WeatherArgs(location="Nowhereville"), USER)

    down = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=down) as client:
        with pytest.raises(ToolError, match="unavailable"):
            await WeatherTool(client).run(WeatherArgs(location="Pune"), USER)


# --------------------------------------------------------------- memory tools


async def test_memory_tools_save_then_search_for_consenting_user() -> None:
    store = SQLiteMemoryStore(":memory:", FakeEmbedder())
    try:
        assert (
            await SaveMemoryTool(store).run(SaveMemoryArgs(content="Plays the guitar"), USER)
            == "Saved."
        )
        found = await SearchMemoryTool(store).run(SearchMemoryArgs(query="guitar"), USER)
        assert found == "- Plays the guitar"
        assert (await store.list("alice"))[0].category == "explicit"
    finally:
        store.close()


async def test_memory_tools_do_nothing_when_memory_is_off() -> None:
    store = SQLiteMemoryStore(":memory:")
    try:
        saved = await SaveMemoryTool(store).run(SaveMemoryArgs(content="secret"), MEMORY_OFF)
        assert "turned off" in saved and await store.list("alice") == []
        await store.add("alice", "Plays the guitar", "interest")
        searched = await SearchMemoryTool(store).run(SearchMemoryArgs(query="guitar"), MEMORY_OFF)
        assert "turned off" in searched and "guitar" not in searched
        empty = await SearchMemoryTool(store).run(SearchMemoryArgs(query="submarines"), USER)
        assert empty == "No matching memories."
    finally:
        store.close()


# ------------------------------------------------------------ default wiring


async def test_default_tool_set_is_the_documented_allowlist() -> None:
    store = SQLiteMemoryStore(":memory:")
    try:
        async with httpx.AsyncClient() as client:
            registry = ToolRegistry(build_default_tools(store, client))
        assert sorted(s.name for s in registry.specs()) == [
            "calculator",
            "fetch_webpage",
            "get_current_datetime",
            "get_weather",
            "save_memory",
            "search_memory",
        ]
    finally:
        store.close()
