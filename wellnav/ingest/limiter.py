"""Cross-process GIS request pacing so partition workers share one request stream."""

from __future__ import annotations

import time
from multiprocessing.synchronize import Lock as MpLock
from typing import Any

_lock: MpLock | None = None
_last: Any = None
_min_interval = 0.0


def init_limiter(lock: MpLock, last: Any, min_interval: float) -> None:
    global _lock, _last, _min_interval
    _lock = lock
    _last = last
    _min_interval = max(0.0, float(min_interval))


def is_active() -> bool:
    return _lock is not None


def acquire(min_interval: float | None = None) -> None:
    """Block until this process may send the next GIS request."""
    interval = _min_interval if min_interval is None else max(0.0, float(min_interval))
    if _lock is None or _last is None:
        if interval:
            time.sleep(interval)
        return
    with _lock:
        now = time.monotonic()
        wait = interval - (now - _last.value)
        if wait > 0:
            time.sleep(wait)
        _last.value = time.monotonic()
