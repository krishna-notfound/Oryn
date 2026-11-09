"""Process-level health numbers for /metrics: event-loop lag and memory.

A single event loop serves every session, so one slow callback delays everyone. The lag ticker
makes that visible: it sleeps 100 ms and records how late it woke up.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
from collections import deque

TICK_S = 0.1
WINDOW_TICKS = 600  # one minute of history


class LoopLagMonitor:
    def __init__(self) -> None:
        self._lags_ms: deque[float] = deque(maxlen=WINDOW_TICKS)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._tick(), name="loop-lag-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait({self._task})
            self._task = None

    def summary(self) -> dict[str, float]:
        """p50 / p99 / max wake-up delay in ms over the last minute; empty before the first tick."""
        if not self._lags_ms:
            return {}
        lags = sorted(self._lags_ms)
        p99 = lags[min(len(lags) - 1, int(len(lags) * 0.99))]
        return {
            "p50": round(statistics.median(lags), 1),
            "p99": round(p99, 1),
            "max": round(lags[-1], 1),
        }

    async def _tick(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time() + TICK_S
        while True:
            await asyncio.sleep(TICK_S)
            now = loop.time()
            self._lags_ms.append(max(0.0, now - expected) * 1000)
            expected = now + TICK_S


def rss_bytes() -> int | None:
    """Resident memory of this process, or None where the platform offers no cheap way."""
    if sys.platform == "win32":
        return None
    try:
        with open("/proc/self/statm") as statm:  # Linux: the production container
            return int(statm.read().split()[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        pass
    import resource  # elsewhere (macOS): reports the peak, the best this fallback has

    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024
