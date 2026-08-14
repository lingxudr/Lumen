"""
Sync worker queue — bounded global concurrency + per-provider semaphore.

Bukan 100 request paralel.
  global workers = 5
  komikcast = 2, komiku = 2, sanka = 1  (via rate_limit.LIMITER)
"""

from __future__ import annotations

import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Job:
    name: str
    fn: Callable[[], Any]
    provider: str | None = None


class SyncQueue:
    def __init__(self, max_workers: int = 5):
        self.max_workers = max(1, int(max_workers))

    def map(self, jobs: list[Job]) -> list[dict[str, Any]]:
        """Jalankan jobs dengan max_workers global; hasil per job."""
        results: list[dict[str, Any]] = []
        if not jobs:
            return results

        def run(job: Job) -> dict[str, Any]:
            try:
                val = job.fn()
                return {"name": job.name, "provider": job.provider, "ok": True, "value": val}
            except Exception as e:
                return {
                    "name": job.name,
                    "provider": job.provider,
                    "ok": False,
                    "error": str(e),
                    "trace": traceback.format_exc()[-400:],
                }

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(run, j): j for j in jobs}
            for fut in as_completed(futs):
                results.append(fut.result())
        return results
