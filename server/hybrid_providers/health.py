"""
Provider health monitoring.

Status:
  healthy  (green)  — error_rate rendah, latency OK
  degraded (yellow) — error_rate / latency tinggi
  down     (red)    — gagal beruntun / circuit open

Manager memakai ini untuk:
  - urutan priority dinamis
  - disable provider yang down
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderHealth:
    name: str
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float | None = None
    last_error: str | None = None
    last_error_kind: str | None = None
    last_check: float | None = None  # unix ts
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0  # unix ts

    def record_success(self, latency_ms: float) -> None:
        self.successes += 1
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self.last_check = time.time()
        self.consecutive_failures = 0
        self.last_error = None
        # half-open recovery
        if self.circuit_open_until and time.time() >= self.circuit_open_until:
            self.circuit_open_until = 0.0

    def record_failure(
        self,
        latency_ms: float,
        error: str,
        *,
        open_for: float | None = None,
        kind: str | None = None,
        force_cooldown: float | None = None,
    ) -> None:
        self.failures += 1
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self.last_check = time.time()
        self.last_error = (error or "")[:240]
        self.last_error_kind = kind
        self.consecutive_failures += 1

        # cooldown eksplisit dari taksonomi (429/403/blocked)
        if force_cooldown and force_cooldown > 0:
            self.circuit_open_until = max(
                self.circuit_open_until, time.time() + force_cooldown
            )
            return

        # default: buka circuit setelah 3 gagal beruntun
        sec = 60.0 if open_for is None else open_for
        if self.consecutive_failures >= 3:
            self.circuit_open_until = time.time() + sec

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def error_rate(self) -> float:
        t = self.total
        if t <= 0:
            return 0.0
        return self.failures / t

    @property
    def avg_latency_ms(self) -> float | None:
        t = self.total
        if t <= 0:
            return None
        return self.total_latency_ms / t

    @property
    def status(self) -> str:
        now = time.time()
        if self.circuit_open_until and now < self.circuit_open_until:
            return "down"
        if self.total == 0:
            return "unknown"
        if self.consecutive_failures >= 3:
            return "down"
        if self.error_rate >= 0.25 or (self.last_latency_ms or 0) >= 5000:
            return "degraded"
        if self.error_rate >= 0.08 or (self.avg_latency_ms or 0) >= 2000:
            return "degraded"
        return "healthy"

    def to_dict(self) -> dict[str, Any]:
        st = self.status
        emoji = {"healthy": "green", "degraded": "yellow", "down": "red", "unknown": "gray"}.get(
            st, "gray"
        )
        age = None
        if self.last_check:
            age = max(0, int(time.time() - self.last_check))
        return {
            "provider": self.name,
            "status": st,
            "status_color": emoji,
            "latency_ms": round(self.last_latency_ms, 1) if self.last_latency_ms is not None else None,
            "avg_latency_ms": round(self.avg_latency_ms, 1) if self.avg_latency_ms is not None else None,
            "error_rate": round(self.error_rate * 100, 2),  # percent
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_error_kind": self.last_error_kind,
            "last_check_ago_sec": age,
            "circuit_open": bool(self.circuit_open_until and time.time() < self.circuit_open_until),
        }


class HealthRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, ProviderHealth] = {}

    def get(self, name: str) -> ProviderHealth:
        with self._lock:
            if name not in self._map:
                self._map[name] = ProviderHealth(name=name)
            return self._map[name]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [h.to_dict() for h in self._map.values()]
        # urut: healthy dulu, lalu nama
        order = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}
        rows.sort(key=lambda r: (order.get(r["status"], 9), r["provider"]))
        return rows


# global registry
REGISTRY = HealthRegistry()
