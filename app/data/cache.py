"""Tiny in-process TTL cache keyed by function name + arguments."""

from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

# TTLs in seconds
SCOREBOARD_TTL = 60
BOX_SCORE_LIVE_TTL = 60
BOX_SCORE_FINAL_TTL = 3600
INJURY_TTL = 600
FANTASY_TTL = 300
PLAYER_STATS_TTL = 600

_store: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


def get(key: tuple) -> Any | None:
    with _lock:
        hit = _store.get(key)
        if hit is None:
            return None
        expires, value = hit
        if time.monotonic() >= expires:
            del _store[key]
            return None
        return value


def put(key: tuple, value: Any, ttl: float) -> None:
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def clear() -> None:
    with _lock:
        _store.clear()


def cached(ttl: float | Callable[[Any], float]):
    """Cache a function's return value. `ttl` may be a callable of the result,
    e.g. to keep finished box scores longer than live ones. Exceptions are not cached."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__qualname__, args, tuple(sorted(kwargs.items())))
            hit = get(key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            put(key, value, ttl(value) if callable(ttl) else ttl)
            return value

        return wrapper

    return deco
