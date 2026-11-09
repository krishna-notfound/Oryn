"""Deterministic cleanup for async iterators handed to us by provider adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def closing[T](iterator: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    """Like `contextlib.aclosing`, for the plain `AsyncIterator` our provider interfaces
    declare: leaving the block closes the generator *now* (releasing the HTTP stream or the
    Timbre slot), not whenever the garbage collector finalises it."""
    try:
        yield iterator
    finally:
        if (aclose := getattr(iterator, "aclose", None)) is not None:
            await aclose()
