"""Tiny in-process TTL cache for slow, slow-changing aggregates.

Note: cache is per-process, so each gunicorn worker keeps its own copy.
That is fine for dashboard stats where a short staleness window is acceptable.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, producer: Callable[[], Any]) -> Any:
    """Return a cached value for `key`, recomputing via `producer` if stale."""
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) < ttl:
            return entry[1]
    value = producer()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def invalidate(key: str | None = None) -> None:
    """Drop one cache entry, or the whole cache when key is None."""
    with _lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)
