# -*- coding: utf-8 -*-
"""SQLite persistence for Lumen — metadata + chapter pages index."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_CONN = None

def _resolve_db_path():
    candidates = []
    env = os.environ.get("DB_PATH") or os.environ.get("LUMEN_DB")
    if env:
        candidates.append(Path(env))
    candidates.append(Path("/data/lumen.db"))
    candidates.append(Path(__file__).resolve().parent.parent / "data" / "lumen.db")
    candidates.append(Path("/tmp/lumen.db"))
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # probe write
            probe = path.parent / ".lumen_write_test"
            probe.write_text("ok")
            probe.unlink(missing_ok=True)
            return path
        except Exception:
            continue
    return Path("/tmp/lumen.db")


DB_PATH = _resolve_db_path()


def _connect():
    global _CONN, DB_PATH
    if _CONN is not None:
        return _CONN
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        DB_PATH = Path("/tmp/lumen.db")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS manga (
            slug TEXT PRIMARY KEY,
            source_id INTEGER,
            title TEXT,
            cover TEXT,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chapter_list (
            slug TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chapter_pages (
            slug TEXT NOT NULL,
            chapter TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (slug, chapter)
        );
        CREATE INDEX IF NOT EXISTS idx_manga_title ON manga(title);
        CREATE INDEX IF NOT EXISTS idx_manga_updated ON manga(updated_at);
        """
    )
    conn.commit()
    _CONN = conn
    return conn


def init_db():
    with _LOCK:
        _connect()
        return str(DB_PATH)


def stats():
    with _LOCK:
        c = _connect()
        manga = c.execute("SELECT COUNT(*) FROM manga").fetchone()[0]
        lists = c.execute("SELECT COUNT(*) FROM chapter_list").fetchone()[0]
        pages = c.execute("SELECT COUNT(*) FROM chapter_pages").fetchone()[0]
        return {
            "path": str(DB_PATH),
            "manga": manga,
            "chapter_lists": lists,
            "chapter_pages": pages,
        }


def _upsert_manga_from_item(item: dict):
    """item = one element of series list/detail API data array or detail.data"""
    if not item:
        return
    # detail shape: { id, data: { slug, title, ... }, ... }
    # list shape: same
    data = item.get("data") or {}
    slug = data.get("slug")
    if not slug:
        return
    title = data.get("title") or slug
    cover = data.get("coverImage") or data.get("cover") or ""
    source_id = item.get("id")
    payload = json.dumps(item, ensure_ascii=False)
    c = _connect()
    c.execute(
        """
        INSERT INTO manga(slug, source_id, title, cover, payload, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(slug) DO UPDATE SET
            source_id=excluded.source_id,
            title=excluded.title,
            cover=excluded.cover,
            payload=excluded.payload,
            updated_at=excluded.updated_at
        """,
        (slug, source_id, title, cover, payload, time.time()),
    )


def save_series_response(body: bytes):
    """Parse upstream series JSON and store manga rows."""
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return
    with _LOCK:
        c = _connect()
        data = obj.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    _upsert_manga_from_item(item)
        elif isinstance(data, dict):
            _upsert_manga_from_item(data)
        c.commit()


def save_chapter_list(slug: str, body: bytes):
    if not slug:
        return
    try:
        # validate json
        json.loads(body.decode("utf-8"))
    except Exception:
        return
    with _LOCK:
        c = _connect()
        c.execute(
            """
            INSERT INTO chapter_list(slug, payload, updated_at)
            VALUES(?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (slug, body.decode("utf-8"), time.time()),
        )
        c.commit()


def save_chapter_pages(slug: str, chapter: str, body: bytes):
    if not slug or chapter is None:
        return
    try:
        json.loads(body.decode("utf-8"))
    except Exception:
        return
    with _LOCK:
        c = _connect()
        c.execute(
            """
            INSERT INTO chapter_pages(slug, chapter, payload, updated_at)
            VALUES(?,?,?,?)
            ON CONFLICT(slug, chapter) DO UPDATE SET
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (slug, str(chapter), body.decode("utf-8"), time.time()),
        )
        c.commit()


def get_manga(slug: str, max_age: float = 7 * 86400):
    with _LOCK:
        c = _connect()
        row = c.execute(
            "SELECT payload, updated_at FROM manga WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            return None
        if time.time() - row["updated_at"] > max_age:
            return None
        return row["payload"].encode("utf-8")


def get_newest_list(limit: int = 20, max_age: float = 14 * 86400):
    """Rebuild series list dari cache manga (upstream 503)."""
    limit = max(1, min(int(limit or 20), 50))
    with _LOCK:
        c = _connect()
        rows = c.execute(
            """
            SELECT payload, updated_at FROM manga
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    now = time.time()
    for row in rows:
        if max_age and now - row["updated_at"] > max_age:
            continue
        try:
            item = json.loads(row["payload"])
            if isinstance(item, dict):
                items.append(item)
        except Exception:
            continue
    if not items:
        return None
    payload = {
        "status": 200,
        "message": "Cached series list (upstream unavailable)",
        "data": items,
        "meta": {"source": "sqlite_cache", "total": len(items), "stale": True},
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def get_chapter_list(slug: str, max_age: float = 3 * 86400):
    with _LOCK:
        c = _connect()
        row = c.execute(
            "SELECT payload, updated_at FROM chapter_list WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            return None
        if time.time() - row["updated_at"] > max_age:
            return None
        return row["payload"].encode("utf-8")


def get_chapter_pages(slug: str, chapter: str, max_age: float = 7 * 86400):
    with _LOCK:
        c = _connect()
        row = c.execute(
            "SELECT payload, updated_at FROM chapter_pages WHERE slug=? AND chapter=?",
            (slug, str(chapter)),
        ).fetchone()
        if not row:
            return None
        if time.time() - row["updated_at"] > max_age:
            return None
        return row["payload"].encode("utf-8")


def wrap_manga_detail(payload_bytes: bytes) -> bytes:
    """DB stores item; API expects {status,data:item}."""
    try:
        item = json.loads(payload_bytes.decode("utf-8"))
        if "status" in item and "data" in item:
            return payload_bytes
        out = {
            "status": 200,
            "message": "Series retrieved from cache",
            "data": item,
            "meta": {"source": "sqlite"},
        }
        return json.dumps(out, ensure_ascii=False).encode("utf-8")
    except Exception:
        return payload_bytes


def search_manga(query: str, limit: int = 20):
    """Local title search (case-insensitive LIKE)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    like = "%%%s%%" % q.replace("%", "").replace("_", "")
    with _LOCK:
        c = _connect()
        rows = c.execute(
            """
            SELECT slug, title, cover, payload, updated_at
            FROM manga
            WHERE title LIKE ? OR slug LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (like, like, int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        try:
            item = json.loads(r["payload"])
        except Exception:
            item = {
                "id": None,
                "data": {"slug": r["slug"], "title": r["title"], "coverImage": r["cover"]},
            }
        out.append(item)
    return out


def prune(max_age_pages: float = 30 * 86400, max_age_lists: float = 14 * 86400, max_pages: int = 4000):
    """Remove old chapter pages/lists; keep manga metadata longer."""
    now = time.time()
    with _LOCK:
        c = _connect()
        c.execute(
            "DELETE FROM chapter_pages WHERE updated_at < ?",
            (now - max_age_pages,),
        )
        c.execute(
            "DELETE FROM chapter_list WHERE updated_at < ?",
            (now - max_age_lists,),
        )
        # hard cap rows
        n = c.execute("SELECT COUNT(*) FROM chapter_pages").fetchone()[0]
        if n > max_pages:
            c.execute(
                """
                DELETE FROM chapter_pages WHERE rowid IN (
                    SELECT rowid FROM chapter_pages
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (n - max_pages,),
            )
        c.commit()
        return stats()
