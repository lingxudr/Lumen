"""
Cache invalidation strategy for Lumen API.

Layers:
  1. In-process API_CACHE (tagged entries)
  2. Client localStorage (api.js) — version-aware
  3. Image cache (long TTL; URL may expire → page_cache needs_refetch)

Policy (TTL) — target freshness ~5 menit untuk data katalog:
  list/latest     soft 90s / hard 5m
  series detail   soft 5m  / hard 1h
  chapter list    soft 2m  / hard 5m
  chapter pages   soft 30m / hard 1d   (gambar jarang berubah)
  genres/tax      soft 30m / hard 12h

Invalidation:
  - tag-based: invalidate("list"), invalidate("series:slug")
  - cascade: series:{slug} also drops chapters:{slug}
  - global generation bump → all clients drop stale keys
  - force=?1 query bypasses soft cache (still serves if hard valid only when not force)

Stale-while-revalidate:
  get() may return (body, stale=True) when soft expired but hard not;
  caller should refresh in background if needed.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

# Global generation — bump on deploy / manual flush
_GENERATION = 1
_LOCK = threading.RLock()

# key → {body, soft_exp, hard_exp, tags, gen}
_STORE: dict[str, dict[str, Any]] = {}
_TAG_INDEX: dict[str, set[str]] = {}
_MAX = 400

# soft, hard seconds
TTL_TABLE = {
    # soft = anggap segar; hard = max age (stale-while-revalidate)
    # hard list/chapters dibatasi ~5 menit agar update tidak terlalu lama
    "list": (90, 300),           # Terbaru / browse
    "series": (300, 3600),       # detail manga
    "chapters": (120, 300),      # daftar chapter
    "pages": (1800, 24 * 3600),  # gambar chapter
    "meta": (1800, 12 * 3600),   # genres
    "default": (90, 300),
}


def generation() -> int:
    return _GENERATION


def bump_generation() -> int:
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        return _GENERATION


def _kind_for(sub_path: str) -> str:
    s = (sub_path or "").split("?")[0].strip("/")
    if s == "series" or s.startswith("series?"):
        return "list"
    if "/chapters/" in s and s.count("/") >= 3:
        return "pages"
    if s.endswith("/chapters") or "/chapters" in s:
        return "chapters"
    if s.startswith("series/"):
        return "series"
    if "genre" in s or "tag" in s:
        return "meta"
    return "default"


def ttl_for(sub_path: str) -> tuple[int, int]:
    soft, hard = TTL_TABLE[_kind_for(sub_path)]
    return soft, hard


def tags_for(sub_path: str) -> list[str]:
    s = (sub_path or "").split("?")[0].strip("/")
    tags = ["all", _kind_for(s)]
    m = re.match(r"series/([^/]+)", s)
    if m:
        slug = m.group(1)
        tags.append(f"series:{slug}")
        if "/chapters" in s:
            tags.append(f"chapters:{slug}")
        if "/chapters/" in s:
            # series/{slug}/chapters/{idx}
            parts = s.split("/")
            if len(parts) >= 4:
                tags.append(f"pages:{slug}:{parts[3]}")
    if s == "series" or s.startswith("series?"):
        tags.append("list")
    return tags


def _evict_if_needed() -> None:
    if len(_STORE) < _MAX:
        return
    now = time.time()
    # drop expired hard
    for k, v in list(_STORE.items()):
        if now > v["hard_exp"]:
            _drop_key(k)
    if len(_STORE) < _MAX:
        return
    # drop oldest soft
    ordered = sorted(_STORE.items(), key=lambda kv: kv[1].get("soft_exp", 0))
    for k, _ in ordered[: max(1, _MAX // 4)]:
        _drop_key(k)


def _drop_key(key: str) -> None:
    row = _STORE.pop(key, None)
    if not row:
        return
    for tag in row.get("tags") or []:
        s = _TAG_INDEX.get(tag)
        if s is not None:
            s.discard(key)
            if not s:
                _TAG_INDEX.pop(tag, None)


def cache_set(key: str, body: bytes, sub_path: str, *, soft: int | None = None, hard: int | None = None) -> None:
    soft_ttl, hard_ttl = ttl_for(sub_path)
    if soft is not None:
        soft_ttl = soft
    if hard is not None:
        hard_ttl = hard
    if hard_ttl < soft_ttl:
        hard_ttl = soft_ttl
    tags = tags_for(sub_path)
    now = time.time()
    with _LOCK:
        _evict_if_needed()
        # remove old index
        if key in _STORE:
            _drop_key(key)
        _STORE[key] = {
            "body": body,
            "soft_exp": now + soft_ttl,
            "hard_exp": now + hard_ttl,
            "tags": tags,
            "gen": _GENERATION,
            "sub": sub_path,
            "soft_ttl": soft_ttl,
            "hard_ttl": hard_ttl,
        }
        for tag in tags:
            _TAG_INDEX.setdefault(tag, set()).add(key)


def cache_get(
    key: str,
    *,
    allow_stale: bool = True,
    min_generation: int | None = None,
) -> tuple[bytes, dict[str, Any]] | None:
    """
    Returns (body, meta) or None.
    meta: stale, age_left_hard, soft_ttl, hard_ttl, gen
    """
    with _LOCK:
        row = _STORE.get(key)
        if not row:
            return None
        now = time.time()
        if min_generation is not None and row.get("gen", 0) < min_generation:
            _drop_key(key)
            return None
        if now > row["hard_exp"]:
            _drop_key(key)
            return None
        stale = now > row["soft_exp"]
        if stale and not allow_stale:
            return None
        meta = {
            "stale": stale,
            "age_left_hard": max(0, int(row["hard_exp"] - now)),
            "soft_ttl": row.get("soft_ttl"),
            "hard_ttl": row.get("hard_ttl"),
            "gen": row.get("gen"),
            "tags": list(row.get("tags") or []),
        }
        return row["body"], meta


def invalidate(*tags: str) -> int:
    """Drop all entries carrying any of the tags. Returns count removed."""
    removed = 0
    with _LOCK:
        keys: set[str] = set()
        for tag in tags:
            keys |= set(_TAG_INDEX.get(tag) or ())
        for k in keys:
            if k in _STORE:
                _drop_key(k)
                removed += 1
    return removed


def invalidate_series(slug: str) -> int:
    """Cascade: series + chapters + pages for slug."""
    return invalidate(f"series:{slug}", f"chapters:{slug}")


def invalidate_list() -> int:
    return invalidate("list")


def flush_all() -> int:
    with _LOCK:
        n = len(_STORE)
        _STORE.clear()
        _TAG_INDEX.clear()
    bump_generation()
    return n


def stats() -> dict[str, Any]:
    with _LOCK:
        now = time.time()
        fresh = sum(1 for v in _STORE.values() if now <= v["soft_exp"])
        stale = sum(1 for v in _STORE.values() if v["soft_exp"] < now <= v["hard_exp"])
        return {
            "entries": len(_STORE),
            "fresh": fresh,
            "stale": stale,
            "tags": len(_TAG_INDEX),
            "generation": _GENERATION,
            "max": _MAX,
        }
