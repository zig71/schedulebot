"""Login throttling.

In-process and deliberately small: this runs as one process against one database,
so a shared cache would be infrastructure for no benefit. The limit is per
(username, address) pair so one attacker cannot lock a real user out of their own
account by hammering it from elsewhere.
"""

from __future__ import annotations

import time
from collections import deque


class LoginThrottle:
    def __init__(self, *, limit: int = 5, window_seconds: int = 900) -> None:
        self._limit = limit
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        stamps = self._failures.setdefault(key, deque())
        cutoff = time.monotonic() - self._window
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        return stamps

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self._limit

    def retry_after(self, key: str) -> int:
        stamps = self._recent(key)
        if not stamps:
            return 0
        return max(0, int(self._window - (time.monotonic() - stamps[0])))

    def fail(self, key: str) -> None:
        self._recent(key).append(time.monotonic())

    def succeed(self, key: str) -> None:
        self._failures.pop(key, None)
