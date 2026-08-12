"""
MongoDB cache layer untuk hybrid providers.

Env:
  MONGO_URI=mongodb://localhost:27017
  MONGO_DB=lumen_comic

Collections:
  manga, chapters, pages, meta

Semua fungsi aman: jika Mongo down / belum di-set, return None (fallback ke scrape).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

_client = None
_db = None
_init_error: str | None = None

# TTL default (detik)
TTL_LATEST = 15 * 60
TTL_MANGA = 60 * 60
TTL_CHAPTERS = 30 * 60
TTL_PAGES = 6 * 60 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return bool(os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI"))


def get_db():
    """Lazy connect. Return db or None."""
    global _client, _db, _init_error
    if _db is not None:
        return _db
    if _init_error and not enabled():
        return None
    uri = os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")
    if not uri:
        _init_error = "MONGO_URI not set"
        return None
    try:
        from pymongo import MongoClient, ASCENDING
        from pymongo.errors import PyMongoError

        _client = MongoClient(uri, serverSelectionTimeoutMS=4000)
        # probe
        _client.admin.command("ping")
        name = os.environ.get("MONGO_DB") or os.environ.get("MONGODB_DB") or "lumen_comic"
        _db = _client[name]
        # indexes (idempotent)
        _db.manga.create_index([("provider", ASCENDING), ("slug", ASCENDING)], unique=True)
        _db.chapters.create_index([("provider", ASCENDING), ("slug", ASCENDING)], unique=True)
        _db.pages.create_index(
            [("provider", ASCENDING), ("slug", ASCENDING), ("number", ASCENDING)],
            unique=True,
        )
        _db.latest.create_index([("provider", ASCENDING), ("page", ASCENDING)], unique=True)
        _init_error = None
        return _db
    except Exception as e:
        _init_error = str(e)
        _db = None
        _client = None
        return None


def status() -> dict[str, Any]:
    db = get_db()
    if db is None:
        return {
            "ok": False,
            "enabled": enabled(),
            "error": _init_error or "not connected",
        }
    try:
        db.command("ping")
        return {
            "ok": True,
            "enabled": True,
            "db": db.name,
            "collections": db.list_collection_names(),
        }
    except Exception as e:
        return {"ok": False, "enabled": True, "error": str(e)}


def _fresh(doc: dict | None, ttl: int) -> dict | None:
    if not doc:
        return None
    cached_at = doc.get("cached_at")
    if cached_at is None:
        return None
    if isinstance(cached_at, datetime):
        ts = cached_at.timestamp()
    else:
        try:
            ts = float(cached_at)
        except (TypeError, ValueError):
            return None
    if time.time() - ts > ttl:
        return None
    return doc


# ---------------------------------------------------------------------------
# latest
# ---------------------------------------------------------------------------

def cache_get_latest(provider: str, page: int = 1) -> list[dict] | None:
    db = get_db()
    if db is None:
        return None
    try:
        doc = db.latest.find_one({"provider": provider, "page": page})
        doc = _fresh(doc, TTL_LATEST)
        if not doc:
            return None
        return doc.get("items")
    except Exception:
        return None


def cache_set_latest(provider: str, page: int, items: list[dict]) -> None:
    db = get_db()
    if db is None:
        return
    try:
        db.latest.update_one(
            {"provider": provider, "page": page},
            {
                "$set": {
                    "provider": provider,
                    "page": page,
                    "items": items,
                    "cached_at": _now(),
                    "count": len(items),
                }
            },
            upsert=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# manga detail
# ---------------------------------------------------------------------------

def cache_get_manga(provider: str, slug: str) -> dict | None:
    db = get_db()
    if db is None:
        return None
    try:
        doc = db.manga.find_one({"provider": provider, "slug": slug})
        doc = _fresh(doc, TTL_MANGA)
        if not doc:
            return None
        return doc.get("data")
    except Exception:
        return None


def cache_set_manga(provider: str, slug: str, data: dict) -> None:
    db = get_db()
    if db is None:
        return
    try:
        db.manga.update_one(
            {"provider": provider, "slug": slug},
            {
                "$set": {
                    "provider": provider,
                    "slug": slug,
                    "data": data,
                    "cached_at": _now(),
                }
            },
            upsert=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# chapters
# ---------------------------------------------------------------------------

def cache_get_chapters(provider: str, slug: str) -> list[dict] | None:
    db = get_db()
    if db is None:
        return None
    try:
        doc = db.chapters.find_one({"provider": provider, "slug": slug})
        doc = _fresh(doc, TTL_CHAPTERS)
        if not doc:
            return None
        return doc.get("items")
    except Exception:
        return None


def cache_set_chapters(provider: str, slug: str, items: list[dict]) -> None:
    db = get_db()
    if db is None:
        return
    try:
        db.chapters.update_one(
            {"provider": provider, "slug": slug},
            {
                "$set": {
                    "provider": provider,
                    "slug": slug,
                    "items": items,
                    "cached_at": _now(),
                    "count": len(items),
                }
            },
            upsert=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

def cache_get_pages(provider: str, slug: str, number: float) -> dict | None:
    db = get_db()
    if db is None:
        return None
    try:
        doc = db.pages.find_one({"provider": provider, "slug": slug, "number": float(number)})
        doc = _fresh(doc, TTL_PAGES)
        if not doc:
            return None
        return doc.get("data")
    except Exception:
        return None


def cache_set_pages(provider: str, slug: str, number: float, data: dict) -> None:
    db = get_db()
    if db is None:
        return
    try:
        db.pages.update_one(
            {"provider": provider, "slug": slug, "number": float(number)},
            {
                "$set": {
                    "provider": provider,
                    "slug": slug,
                    "number": float(number),
                    "data": data,
                    "cached_at": _now(),
                }
            },
            upsert=True,
        )
    except Exception:
        pass
