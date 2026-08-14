"""
Rate limit / concurrency per provider.

Jangan: 100 manga × 10 chapters × 3 providers paralel.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

# max concurrent in-flight requests per provider
DEFAULT_LIMITS: dict[str, int] = {
    "komikcast": 2,
    "komiku": 3,
    "sanka": 5,
    "default": 3,
}

# minimum gap between request starts (seconds)
DEFAULT_MIN_INTERVAL: dict[str, float] = {
    "komikcast": 0.15,
    "komiku": 0.1,
    "sanka": 0.05,
    "default": 0.1,
}


class ProviderRateLimiter:
    def __init__(
        self,
        limits: dict[str, int] | None = None,
        min_interval: dict[str, float] | None = None,
    ):
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._interval = {**DEFAULT_MIN_INTERVAL, **(min_interval or {})}
        self._sems: dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()
        self._last_start: dict[str, float] = {}

    def _sem(self, name: str) -> threading.Semaphore:
        with self._lock:
            if name not in self._sems:
                n = int(self._limits.get(name) or self._limits["default"])
                self._sems[name] = threading.Semaphore(max(1, n))
            return self._sems[name]

    def _pace(self, name: str) -> None:
        gap = float(self._interval.get(name) or self._interval["default"])
        with self._lock:
            last = self._last_start.get(name, 0.0)
            now = time.monotonic()
            wait = gap - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._last_start[name] = time.monotonic()

    @contextmanager
    def acquire(self, provider: str) -> Iterator[None]:
        sem = self._sem(provider)
        sem.acquire()
        try:
            self._pace(provider)
            yield
        finally:
            sem.release()

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                name: {
                    "max_concurrency": int(self._limits.get(name) or self._limits["default"]),
                    "min_interval_sec": float(self._interval.get(name) or self._interval["default"]),
                }
                for name in set(list(self._limits) + list(self._sems))
                if name != "default"
            }


# process-wide
LIMITER = ProviderRateLimiter()
