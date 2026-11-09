"""Token bucket used to cap how fast one socket may send frames."""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        capacity: float,
        refill_per_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill_per_s = refill_per_s
        self._clock = clock
        self._tokens = capacity
        self._updated = clock()

    def allow(self) -> bool:
        """Spend one token; False means the caller is over its budget."""
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_s)
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True
