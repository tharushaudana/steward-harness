"""Adapt a synchronous store (e.g. a Django ORM or sqlite3 implementation) to steward's async protocols.

    class MyRecords:                       # plain sync methods with the protocol's names and signatures
        def append(self, record): ...
        def list(self, type=None, limit=200, include_test=False): ...
        def set_status(self, record_id, status): ...

    stores.records = wrap(MyRecords())

Each call runs in a worker thread (asyncio.to_thread), so blocking I/O never stalls the event loop.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any


class _AsyncWrapper:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or inspect.iscoroutinefunction(attr):
            return attr

        @functools.wraps(attr)
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(attr, *args, **kwargs)

        return call

    def __repr__(self) -> str:
        return f"wrap({self._inner!r})"


def wrap(sync_store: Any) -> Any:
    """Return an object whose methods are async versions of `sync_store`'s methods."""
    return _AsyncWrapper(sync_store)
