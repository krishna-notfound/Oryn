"""Oryn's built-in tools. `web_search` is intentionally absent: no verified provider/key yet."""

from __future__ import annotations

from typing import Any

import httpx

from app.memory.store import SQLiteMemoryStore
from app.tools.builtin.calculator import CalculatorTool
from app.tools.builtin.clock import CurrentDateTimeTool
from app.tools.builtin.memory_tools import SaveMemoryTool, SearchMemoryTool
from app.tools.builtin.weather import WeatherTool
from app.tools.builtin.webpage import FetchWebpageTool
from app.tools.registry import Tool


def build_default_tools(
    store: SQLiteMemoryStore, http_client: httpx.AsyncClient
) -> list[Tool[Any]]:
    """The full allowlist. The caller owns `http_client` and closes it on shutdown."""
    return [
        CurrentDateTimeTool(),
        CalculatorTool(),
        WeatherTool(http_client),
        FetchWebpageTool(http_client),
        SearchMemoryTool(store),
        SaveMemoryTool(store),
    ]
