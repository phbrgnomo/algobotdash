"""Process-local admission limits for the dashboard's expensive series reads."""

import math
import threading
import time
from collections import deque
from functools import lru_cache


class SeriesBusyError(RuntimeError):
    """Reject work before opening SQLite or allocating a chart sample."""

    def __init__(self, retry_after: int):
        super().__init__("series_rate_limit")
        self.retry_after = retry_after


class SeriesBudget:
    """Allow two concurrent requests and thirty starts per minute per projection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._starts: deque[float] = deque()
        self._active = 0

    def start(self) -> None:
        """Admit immediately or reject without building a background queue."""
        with self._lock:
            now = time.monotonic()
            while self._starts and self._starts[0] <= now - 60:
                self._starts.popleft()
            if self._active >= 2:
                raise SeriesBusyError(1)
            if len(self._starts) >= 30:
                raise SeriesBusyError(max(1, math.ceil(self._starts[0] + 60 - now)))
            self._starts.append(now)
            self._active += 1

    def finish(self) -> None:
        """Release concurrency only after the worker has actually stopped."""
        with self._lock:
            self._active -= 1


@lru_cache(maxsize=16)
def series_budget(database: str) -> SeriesBudget:
    """Bound bookkeeping while sharing one budget across local clients."""
    _ = database
    return SeriesBudget()
