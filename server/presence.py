"""In-memory active visitor presence (Railway process)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

# session_id -> record
_store: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

# consider online if heartbeat within this window
ONLINE_SEC = 120
MAX_SESSIONS = 2000


def heartbeat(
    session_id: str,
    *,
    path: str = "/",
    ip: str = "",
    ua: str = "",
) -> dict[str, Any]:
    sid = (session_id or "").strip()[:64] or str(uuid.uuid4())
    now = time.time()
    with _lock:
        _store[sid] = {
            "id": sid[:8],
            "path": (path or "/")[:200],
            "ip": (ip or "")[:64],
            "ua": (ua or "")[:120],
            "last": now,
        }
        if len(_store) > MAX_SESSIONS:
            # drop oldest
            ordered = sorted(_store.items(), key=lambda x: x[1].get("last", 0))
            for k, _ in ordered[: len(_store) - MAX_SESSIONS]:
                _store.pop(k, None)
    return {"ok": True, "session": sid, "online": count_online()}


def _prune(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    dead = [k for k, v in _store.items() if now - float(v.get("last") or 0) > ONLINE_SEC]
    for k in dead:
        _store.pop(k, None)


def count_online() -> int:
    now = time.time()
    with _lock:
        _prune(now)
        return len(_store)


def list_online(limit: int = 30) -> list[dict[str, Any]]:
    now = time.time()
    with _lock:
        _prune(now)
        items = sorted(_store.values(), key=lambda x: -float(x.get("last") or 0))
        out = []
        for v in items[: max(1, min(limit, 50))]:
            age = int(now - float(v.get("last") or now))
            out.append(
                {
                    "id": v.get("id"),
                    "path": v.get("path"),
                    "ip": v.get("ip"),
                    "age_sec": age,
                }
            )
        return out


def summary_text() -> str:
    n = count_online()
    rows = list_online(15)
    if n == 0:
        return "🟢 Online: 0\nTidak ada pengunjung aktif saat ini."
    lines = [f"🟢 Online: {n}", "————————————"]
    for i, r in enumerate(rows, 1):
        path = r.get("path") or "/"
        ip = r.get("ip") or "?"
        age = r.get("age_sec", 0)
        lines.append(f"{i}. {path}\n   IP {ip} · {age}s lalu")
    if n > len(rows):
        lines.append(f"… +{n - len(rows)} lainnya")
    return "\n".join(lines)
