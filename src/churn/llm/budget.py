"""Per-run cap on model calls. Cache hits and template fallbacks do not consume it."""

from __future__ import annotations

import threading


class CallBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True
