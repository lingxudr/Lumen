"""
Shinigami / Izanami API client (api.shngm.io/v1).

Public endpoints (no auth observed at draft time):
  GET /manga/list?page=&page_size=&q=
  GET /manga/top
  GET /genre/list
  GET /manga/detail/{manga_id}
  GET /chapter/{manga_id}/list?page=&page_size=
  GET /chapter/detail/{chapter_id}

IDs are UUIDs. Output is normalized toward Lumen/Voratoon-shaped payloads
so ProviderManager can merge later.

Env:
  SHINIGAMI_API   default https://api.shngm.io/v1
  SHINIGAMI_TIMEOUT  default 12
  SCRAPER_USER_AGENT
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = (os.environ.get("SHINIGAMI_API") or "https://api.shngm.io/v1").rstrip("/")
UA = os.environ.get(
    "SCRAPER_USER_AGENT",
    "LumenReader/2.0 (metadata; respectful caching)",
)
TIMEOUT = float(os.environ.get("SHINIGAMI_TIMEOUT", "12"))
ORIGIN = os.environ.get("SHINIGAMI_ORIGIN", "https://shinigami.ae")

_MEM_LOCK = threading.Lock()
_MEM: dict[str, tuple[float, Any]] = {}
_MEM_MAX = 96

_RE_SLUG = re.compile(r"[^a-z0-9]+")


def _mem_get(key: str):
    now = time.time()
    with _MEM_LOCK:
        row = _MEM.get(key)
        if not row:
            return None
        exp, val = row
        if now > exp:
            _MEM.pop(key, None)
            return None
        return val


def _mem_set(key: str, val: Any, ttl: float) -> None:
    with _MEM_LOCK:
        if len(_MEM) >= _MEM_MAX:
            items = sorted(_MEM.items(), key=lambda kv: kv[1][0])
            for k, _ in items[: max(1, _MEM_MAX // 2)]:
                _MEM.pop(k, None)
        _MEM[key] = (time.time() + ttl, val)


def _slugify(title: str, manga_id: str = "") -> str:
    s = (title or "").lower().strip()
    s = _RE_SLUG.sub("-", s).strip("-")
    if not s and manga_id:
        s = manga_id[:8]
    return s or "unknown"


def _get(path: str, params: dict | None = None, *, ttl: float = 45.0) -> dict[str, Any]:
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and v != ""})
    url = f"{BASE}{path}" + (f"?{qs}" if qs else "")
    cache_key = "GET " + url
    hit = _mem_get(cache_key)
    if hit is not None:
        return hit

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            "Origin": ORIGIN,
            "Referer": ORIGIN.rstrip("/") + "/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"[shinigami] HTTP {e.code}: {path} {body}") from e
    except Exception as e:
        raise RuntimeError(f"[shinigami] GET failed: {path} ({e})") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"[shinigami] non-object JSON: {path}")
    if data.get("retcode") not in (0, None, "0") and data.get("message") not in (
        "success",
        None,
    ):
        # some success responses use retcode 0 only
        if data.get("retcode") not in (0, "0"):
            raise RuntimeError(
                f"[shinigami] API error retcode={data.get('retcode')} msg={data.get('message')}"
            )

    _mem_set(cache_key, data, ttl)
    return data


def _status_map(raw: Any) -> str:
    s = str(raw or "").lower()
    if s in ("completed", "complete", "finished", "end"):
        return "completed"
    if s in ("ongoing", "on-going", "publishing", "1"):
        return "ongoing"
    return s or "ongoing"


def _normalize_series(item: dict) -> dict[str, Any]:
    """Map Shinigami manga card → Lumen nested series item."""
    mid = str(item.get("manga_id") or item.get("id") or "")
    title = (item.get("title") or "").strip() or "Untitled"
    slug = _slugify(title, mid)
    cover = (
        item.get("cover_portrait_url")
        or item.get("cover_image_url")
        or item.get("cover")
        or ""
    )
    latest_n = item.get("latest_chapter_number")
    try:
        latest_n = float(latest_n) if latest_n is not None else None
    except Exception:
        latest_n = None

    data = {
        "title": title,
        "nativeTitle": item.get("alternative_title") or "",
        "slug": slug,
        "coverImage": cover,
        "backgroundImage": item.get("cover_image_url") or cover,
        "status": _status_map(item.get("status")),
        "description": item.get("description") or "",
        "rating": item.get("user_rate") or item.get("rating"),
        "views": item.get("view_count"),
        "releaseYear": item.get("release_year"),
        "country": item.get("country_id"),
        "providerId": mid,
        "provider": "shinigami",
        "latestChapterLabel": f"Chapter {latest_n:g}" if latest_n is not None else None,
        "updatedLabel": item.get("latest_chapter_time") or item.get("updated_at"),
    }
    # taxonomy → genres if present
    tax = item.get("taxonomy")
    genres = []
    if isinstance(tax, dict):
        for key in ("genres", "genre", "tags"):
            raw = tax.get(key)
            if isinstance(raw, list):
                for g in raw:
                    if isinstance(g, dict):
                        name = g.get("name") or g.get("title")
                        if name:
                            genres.append({"name": name, "slug": _slugify(str(name))})
                    elif isinstance(g, str) and g.strip():
                        genres.append({"name": g.strip(), "slug": _slugify(g)})
    if genres:
        data["genres"] = genres

    return {
        "id": mid,
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at") or item.get("latest_chapter_time"),
        "data": data,
        "provider": "shinigami",
        "manga_id": mid,
        "latest_chapter_id": item.get("latest_chapter_id"),
    }


def _normalize_chapter_row(ch: dict) -> dict[str, Any]:
    num = ch.get("chapter_number")
    try:
        idx = float(num) if num is not None else None
    except Exception:
        idx = None
    cid = str(ch.get("chapter_id") or ch.get("id") or "")
    title = (ch.get("chapter_title") or "").strip()
    if not title and idx is not None:
        title = f"Chapter {idx:g}"
    return {
        "id": cid,
        "createdAt": ch.get("release_date") or ch.get("created_at"),
        "updatedAt": ch.get("updated_at") or ch.get("release_date"),
        "data": {
            "slug": None,
            "title": title,
            "index": idx,
            "isDraft": False,
            "thumbnail": ch.get("thumbnail_image_url"),
            "images": [],
        },
        "chapterIndex": idx,
        "provider": "shinigami",
        "chapter_id": cid,
        "views": {"total": str(ch.get("view_count") or "0")},
    }


def _build_page_urls(detail: dict) -> list[str]:
    base = (detail.get("base_url") or "https://assets.shngm.id").rstrip("/")
    chapter = detail.get("chapter") or {}
    path = chapter.get("path") or ""
    if path and not path.startswith("/"):
        path = "/" + path
    files = chapter.get("data") or []
    urls = []
    for name in files:
        if not isinstance(name, str) or not name.strip():
            continue
        # skip watermark / end cards often named 98 / 9999
        low = name.lower()
        if low.startswith("9999") or low.startswith("998"):
            continue
        urls.append(f"{base}{path}{name}")
    return urls


# ---------------------------------------------------------------------------
# Public API (Lumen-shaped)
# ---------------------------------------------------------------------------


def get_series_list(
    *,
    take: int = 20,
    page: int = 1,
    q: str = "",
    mode: str = "newest",
    **_kwargs: Any,
) -> dict[str, Any]:
    """
    List / search / top.
    mode: newest|hot|search (search also if q set)
    """
    page = max(1, int(page or 1))
    take = max(1, min(int(take or 20), 48))
    q = (q or "").strip()
    mode = (mode or "newest").lower()
    if q:
        mode = "search"

    if mode in ("hot", "popular", "top"):
        raw = _get("/manga/top", ttl=120)
        items = raw.get("data") or []
        if not isinstance(items, list):
            items = []
        # top may be full list — slice
        start = (page - 1) * take
        chunk = items[start : start + take]
        norm = [_normalize_series(x) for x in chunk if isinstance(x, dict)]
        return {
            "status": 200,
            "message": "Shinigami top",
            "data": norm,
            "meta": {
                "source": "shinigami_top",
                "page": page,
                "lastPage": max(1, (len(items) + take - 1) // take),
                "total": len(items),
                "provider": "shinigami",
            },
        }

    params = {"page": page, "page_size": take}
    if q:
        params["q"] = q
    raw = _get("/manga/list", params, ttl=60)
    items = raw.get("data") or []
    if not isinstance(items, list):
        items = []
    meta = raw.get("meta") or {}
    norm = [_normalize_series(x) for x in items if isinstance(x, dict)]
    return {
        "status": 200,
        "message": "Shinigami search" if q else "Shinigami list",
        "data": norm,
        "meta": {
            "source": "shinigami_list",
            "page": int(meta.get("page") or page),
            "lastPage": int(meta.get("total_page") or page),
            "total": int(meta.get("total_record") or len(norm)),
            "provider": "shinigami",
            "q": q or "",
        },
    }


def get_series_detail(manga_id: str) -> dict[str, Any] | None:
    """Detail by Shinigami UUID (manga_id)."""
    mid = (manga_id or "").strip()
    if not mid:
        return None
    raw = _get(f"/manga/detail/{urllib.parse.quote(mid)}", ttl=180)
    item = raw.get("data")
    if not isinstance(item, dict):
        return None
    # detail payload uses same fields as list + more
    if not item.get("manga_id"):
        item["manga_id"] = mid
    norm = _normalize_series(item)
    # attach richer description already in data
    return {
        "status": 200,
        "message": "Shinigami detail",
        "data": norm.get("data"),
        "meta": {
            "source": "shinigami_detail",
            "provider": "shinigami",
            "manga_id": mid,
            "latest_chapter_id": item.get("latest_chapter_id"),
            "latest_chapter_number": item.get("latest_chapter_number"),
        },
        "provider": "shinigami",
        "id": mid,
    }


def get_chapters(manga_id: str, *, page: int = 1, page_size: int = 48) -> dict[str, Any]:
    """Paginated chapter list for a manga UUID."""
    mid = (manga_id or "").strip()
    if not mid:
        return {"status": 400, "message": "manga_id required", "data": [], "meta": {}}
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 48), 100))
    raw = _get(
        f"/chapter/{urllib.parse.quote(mid)}/list",
        {"page": page, "page_size": page_size},
        ttl=90,
    )
    rows = raw.get("data") or []
    if not isinstance(rows, list):
        rows = []
    meta = raw.get("meta") or {}
    norm = [_normalize_chapter_row(c) for c in rows if isinstance(c, dict)]
    return {
        "status": 200,
        "message": "Shinigami chapters",
        "data": norm,
        "meta": {
            "source": "shinigami_chapters",
            "page": int(meta.get("page") or page),
            "lastPage": int(meta.get("total_page") or page),
            "total": int(meta.get("total_record") or len(norm)),
            "provider": "shinigami",
            "manga_id": mid,
        },
    }


def get_all_chapters(manga_id: str, *, max_pages: int = 40) -> dict[str, Any]:
    """Fetch all chapter pages (cap max_pages to avoid abuse)."""
    mid = (manga_id or "").strip()
    all_rows: list[dict] = []
    page = 1
    last = 1
    total = 0
    while page <= max_pages and page <= last:
        chunk = get_chapters(mid, page=page, page_size=48)
        rows = chunk.get("data") or []
        all_rows.extend(rows)
        m = chunk.get("meta") or {}
        last = int(m.get("lastPage") or 1)
        total = int(m.get("total") or total)
        if not rows:
            break
        page += 1
    return {
        "status": 200,
        "message": "Shinigami chapters (all)",
        "data": all_rows,
        "meta": {
            "source": "shinigami_chapters_all",
            "page": 1,
            "lastPage": 1,
            "total": total or len(all_rows),
            "provider": "shinigami",
            "manga_id": mid,
        },
    }


def get_pages(chapter_id: str) -> dict[str, Any]:
    """Page image URLs for one chapter UUID."""
    cid = (chapter_id or "").strip()
    if not cid:
        return {"status": 400, "message": "chapter_id required", "data": {"images": []}}
    raw = _get(f"/chapter/detail/{urllib.parse.quote(cid)}", ttl=300)
    detail = raw.get("data") or {}
    if not isinstance(detail, dict):
        return {"status": 404, "message": "chapter not found", "data": {"images": []}}
    images = _build_page_urls(detail)
    num = detail.get("chapter_number")
    try:
        idx = float(num) if num is not None else None
    except Exception:
        idx = None
    return {
        "status": 200,
        "message": "Shinigami pages",
        "data": {
            "chapter_id": cid,
            "manga_id": detail.get("manga_id"),
            "chapter_number": idx,
            "title": detail.get("chapter_title")
            or (f"Chapter {idx:g}" if idx is not None else None),
            "images": images,
            "total_images": len(images),
            "prev_chapter": {
                "chapter_id": detail.get("prev_chapter_id"),
                "chapter_number": detail.get("prev_chapter_number"),
            }
            if detail.get("prev_chapter_id")
            else None,
            "next_chapter": {
                "chapter_id": detail.get("next_chapter_id"),
                "chapter_number": detail.get("next_chapter_number"),
            }
            if detail.get("next_chapter_id")
            else None,
        },
        "meta": {"source": "shinigami_pages", "provider": "shinigami"},
    }


def get_genres() -> dict[str, Any]:
    raw = _get("/genre/list", ttl=3600)
    items = raw.get("data") or []
    out = []
    for g in items if isinstance(items, list) else []:
        if not isinstance(g, dict):
            continue
        name = (g.get("name") or g.get("title") or "").strip()
        if not name:
            continue
        out.append(
            {
                "id": g.get("id"),
                "name": name,
                "slug": g.get("slug") or _slugify(name),
            }
        )
    return {
        "status": 200,
        "message": "Shinigami genres",
        "data": out,
        "meta": {"provider": "shinigami", "total": len(out)},
    }


def health() -> dict[str, Any]:
    t0 = time.time()
    try:
        _get("/manga/list", {"page": 1, "page_size": 1}, ttl=10)
        ms = int((time.time() - t0) * 1000)
        return {"provider": "shinigami", "status": "healthy", "latency_ms": ms}
    except Exception as e:
        return {
            "provider": "shinigami",
            "status": "down",
            "error": str(e)[:200],
            "latency_ms": int((time.time() - t0) * 1000),
        }


# CLI smoke test
if __name__ == "__main__":
    print("health", health())
    lst = get_series_list(take=3, page=1)
    print("list", lst["meta"], [x["data"]["title"] for x in lst["data"][:3]])
    if lst["data"]:
        mid = lst["data"][0]["manga_id"]
        print("detail", get_series_detail(mid)["data"]["title"])
        chs = get_chapters(mid, page=1, page_size=5)
        print("chapters", chs["meta"]["total"], [c["data"]["title"] for c in chs["data"][:3]])
        if chs["data"]:
            pages = get_pages(chs["data"][0]["chapter_id"])
            print("pages", pages["data"]["total_images"], pages["data"]["images"][:2])
