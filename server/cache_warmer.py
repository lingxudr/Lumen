"""
Automatic cache warming for Lumen.

Goals:
  - Pre-fill list/latest so first visitor is not cold
  - Warm top-N series detail + chapter lists
  - Run on startup (delayed) + periodic interval
  - Never blocks request path; runs in daemon thread

Env:
  CACHE_WARM_ON_START=1   (default 1)
  CACHE_WARM_INTERVAL=900 (seconds, 0 = once only)
  CACHE_WARM_TOP=12       (series to warm)
  CACHE_WARM_DELAY=2      (seconds after boot)
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Any, Callable
from urllib.parse import urlencode

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "running": False,
    "last_run": None,
    "last_ok": None,
    "last_error": None,
    "warmed": {},
    "cycles": 0,
}


def _env_bool(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def status() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _set(**kwargs: Any) -> None:
    with _LOCK:
        _STATE.update(kwargs)


def _fetch_and_cache(base: str, sub: str, params: dict | None = None) -> bool:
    """
    Hit local API path through handler logic if possible; else HTTP to self.
    Prefer in-process function injection via warmer_context.
    """
    from cache_policy import cache_set, cache_get  # type: ignore

    q = urlencode(params or {})
    path = sub.strip("/")
    key = f"GET warm://local/{path}" + (f"?{q}" if q else "")
    # If already fresh, skip
    hit = cache_get(key, allow_stale=False)
    if hit is not None:
        return True
    return False  # actual fill done by warm_via_callback


def warm_once(
    *,
    fetch_json: Callable[[str, dict | None], bytes | None],
    top_n: int | None = None,
) -> dict[str, Any]:
    """
    fetch_json(sub_path, query_dict) -> response body bytes or None
    """
    top_n = top_n if top_n is not None else _env_int("CACHE_WARM_TOP", 12)
    stats: dict[str, Any] = {
        "list": False,
        "series": 0,
        "chapters": 0,
        "errors": [],
    }
    _set(running=True, last_error=None)
    t0 = time.time()

    try:
        from cache_policy import cache_set, cache_get  # type: ignore
    except Exception:
        from server.cache_policy import cache_set, cache_get  # type: ignore

    def put(sub: str, body: bytes, params: dict | None = None) -> None:
        """Store under same key shape as app.py: GET {API_BASE}/{sub}?{query}"""
        q = urlencode(params or {}, doseq=True)
        api_base = (os.environ.get("API_BASE") or "https://be.komikcast.cc").rstrip("/")
        key = f"GET {api_base}/{sub}" + (f"?{q}" if q else "")
        cache_set(key, body, sub, soft=None, hard=None)
        # without query variant for detail
        if params:
            key_plain = f"GET {api_base}/{sub}"
            cache_set(key_plain, body, sub, soft=None, hard=None)

    # 1) Latest list
    try:
        params = {"page": "1", "take": "30", "sort": "updatedAt"}
        body = fetch_json("series", params)
        if body:
            put("series", body, params)
            stats["list"] = True
            # popular normalized
            try:
                if fetch_json("popular", {"take": "20", "page": "1"}):
                    stats["popular"] = True
            except Exception as e:
                stats.setdefault("errors", []).append(f"popular: {e}")

    except Exception as e:
        stats["errors"].append(f"list: {e}")

    # 2) Extract slugs from list JSON
    slugs: list[str] = []
    if stats["list"]:
        try:
            import json

            params = {"page": "1", "take": "30", "sort": "updatedAt"}
            body = fetch_json("series", params)
            data = json.loads(body.decode("utf-8", errors="replace"))
            items = data.get("data") or []
            for it in items[:top_n]:
                if not isinstance(it, dict):
                    continue
                inner = it.get("data") if isinstance(it.get("data"), dict) else it
                slug = (
                    (inner or {}).get("slug")
                    or it.get("slug")
                    or it.get("id")
                    or it.get("manga_id")
                )
                if slug:
                    slugs.append(str(slug))
        except Exception as e:
            stats["errors"].append(f"parse list: {e}")

    # 3) Warm detail + chapters for top slugs
    for slug in slugs[:top_n]:
        try:
            body = fetch_json(f"series/{slug}", {"includeMeta": "true"})
            if body:
                put(f"series/{slug}", body, {"includeMeta": "true"})
                # also without query
                put(f"series/{slug}", body, None)
                stats["series"] += 1
        except Exception as e:
            stats["errors"].append(f"series {slug}: {e}")
            continue
        try:
            body = fetch_json(f"series/{slug}/chapters", None)
            if body:
                put(f"series/{slug}/chapters", body, None)
                stats["chapters"] += 1
        except Exception as e:
            stats["errors"].append(f"chapters {slug}: {e}")
        time.sleep(0.05)  # gentle

    elapsed = round(time.time() - t0, 2)
    stats["elapsed_sec"] = elapsed
    _set(
        running=False,
        last_run=time.time(),
        last_ok=time.time() if not stats["errors"] or stats["list"] else None,
        warmed=stats,
        cycles=_STATE.get("cycles", 0) + 1,
        last_error="; ".join(stats["errors"][:3]) if stats["errors"] else None,
    )
    return stats


def _default_fetch_json(sub: str, params: dict | None) -> bytes | None:
    """HTTP fetch against local server or Sanka path via services."""
    try:
        # Prefer in-process Sanka / manager for series list
        if sub == "series":
            try:
                from services.manga_service import sanka_fallback  # type: ignore
            except Exception:
                from server.services.manga_service import sanka_fallback  # type: ignore
            qs = {k: [str(v)] for k, v in (params or {}).items()}
            return sanka_fallback("series", qs)

        if sub.startswith("series/") and sub.endswith("/chapters"):
            slug = sub[len("series/") : -len("/chapters")]
            try:
                from services.manga_service import sanka_fallback
            except Exception:
                from server.services.manga_service import sanka_fallback
            return sanka_fallback(f"series/{slug}/chapters", {})

        if sub.startswith("series/"):
            slug = sub.split("/", 1)[1].split("?")[0]
            try:
                from services.manga_service import sanka_fallback
            except Exception:
                from server.services.manga_service import sanka_fallback
            qs = {k: [str(v)] for k, v in (params or {}).items()}
            return sanka_fallback(f"series/{slug}", qs)
    except Exception:
        traceback.print_exc()
    return None


def start_background_warmer(
    fetch_json: Callable[[str, dict | None], bytes | None] | None = None,
) -> None:
    if not _env_bool("CACHE_WARM_ON_START", True):
        print("[cache_warmer] disabled CACHE_WARM_ON_START=0", flush=True)
        return

    fetch = fetch_json or _default_fetch_json
    delay = _env_int("CACHE_WARM_DELAY", 2)
    interval = _env_int("CACHE_WARM_INTERVAL", 300)

    def loop() -> None:
        time.sleep(max(0, delay))
        while True:
            try:
                print("[cache_warmer] cycle start", flush=True)
                st = warm_once(fetch_json=fetch)
                print("[cache_warmer] done", st, flush=True)
            except Exception as e:
                print("[cache_warmer] error", e, flush=True)
                _set(running=False, last_error=str(e))
            if interval <= 0:
                break
            time.sleep(interval)

    th = threading.Thread(target=loop, name="lumen-cache-warmer", daemon=True)
    th.start()
    print(
        f"[cache_warmer] scheduled delay={delay}s interval={interval}s",
        flush=True,
    )

    # Lightweight keepalive: ping series list every ~60s to reduce Railway sleep impact
    # (only helps while process is already awake; pairs with external uptime ping)
    def keepalive():
        import time as _t
        _t.sleep(max(20, delay))
        while True:
            try:
                fetch("series", {"take": "6", "page": "1", "mode": "newest"})
            except Exception:
                pass
            _t.sleep(45)

    if _env_bool("CACHE_KEEPALIVE", True):
        threading.Thread(target=keepalive, name="lumen-keepalive", daemon=True).start()
        print("[cache_warmer] keepalive every 45s", flush=True)
