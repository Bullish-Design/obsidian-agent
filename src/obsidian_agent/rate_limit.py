from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
import time


@dataclass
class _Bucket:
    timestamps: deque[float] = field(default_factory=deque)


class RouteRateLimiter:
    """Simple in-memory sliding-window limiter keyed by client and route."""

    def __init__(self, *, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        if self.max_events <= 0:
            return True

        now = time.monotonic()
        oldest_allowed = now - self.window_seconds

        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            while bucket.timestamps and bucket.timestamps[0] < oldest_allowed:
                bucket.timestamps.popleft()

            if len(bucket.timestamps) >= self.max_events:
                return False

            bucket.timestamps.append(now)
            return True
