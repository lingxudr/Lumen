"""
Voratoon API client (api.voratoon.com) + site RSC (v1.voratoon.com).

Performance:
  - gzip responses
  - short in-process TTL cache (JSON ~45s, home RSC ~120s)
  - parallel page fetch for newest/new_series
  - tighter timeouts for list vs HTML
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
import re

BASE = (os.environ.get("VORATOON_API") or "https://api.voratoon.com").rstrip("/")
UA = os.environ.get(
    "SCRAPER_USER_AGENT",
    "LumenReader/2.0 (metadata; respectful caching)",
)

TIMEOUT = float(os.environ.get("VORATOON_TIMEOUT", "12"))
TIMEOUT_LIST = float(os.environ.get("VORATOON_TIMEOUT_LIST", "8"))
TIMEOUT_HTML = float(os.environ.get("VORATOON_TIMEOUT_HTML", "10"))
SITE_BASE = (os.environ.get("VORATOON_SITE") or "https://v1.voratoon.com").rstrip("/")

# In-process short cache (reduces repeat RSC/API hits within warm window)
import threading
import time as _time
import gzip as _gzip
from concurrent.futures import ThreadPoolExecutor, as_completed

_MEM_LOCK = threading.Lock()
_MEM: dict[str, tuple[float, Any]] = {}
_MEM_MAX = int(os.environ.get("VORATOON_MEM_MAX", "256"))

# Shared pool — avoid creating ThreadPoolExecutor per request
_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("VORATOON_WORKERS", "4")))

# Keep-alive opener (fewer TCP handshakes to api.voratoon.com / v1.voratoon.com)
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
)

# Precompiled RSC / chapter patterns
_RE_RSC_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_RE_LAST_PAGE = re.compile(r'"lastPage"\s*:\s*(\d+)')
_RE_PAGE = re.compile(r'"page"\s*:\s*(\d+)')
_RE_CH = re.compile(r"(?:chapter|ch\.?|ep\.?|episode)\s*(\d+(?:\.\d+)?)", re.I)
_RE_CH_RANGE = re.compile(r"\b(\d+)\s*[-–]\s*(\d+)\b")
_RE_DIGITS = re.compile(r"(\d+(?:\.\d+)?)")


def _mem_get(key: str):
    now = _time.time()
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
            # drop oldest ~half
            items = sorted(_MEM.items(), key=lambda kv: kv[1][0])
            for k, _ in items[: max(1, _MEM_MAX // 2)]:
                _MEM.pop(k, None)
        _MEM[key] = (_time.time() + ttl, val)


def _read_body(resp) -> bytes:
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc or raw[:2] == b"\x1f\x8b":
        try:
            return _gzip.decompress(raw)
        except Exception:
            return raw
    return raw


def _get_html(url: str, *, ttl: float = 90.0) -> str:
    ck = f"html:{url}"
    hit = _mem_get(ck)
    if hit is not None:
        return hit
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive",
        },
        method="GET",
    )
    with _OPENER.open(req, timeout=TIMEOUT_HTML) as resp:
        text = _read_body(resp).decode("utf-8", errors="replace")
    _mem_set(ck, text, ttl)
    return text



def _decode_biggest_rsc_frame(html: str) -> str | None:
    """Ambil string Flight frame terbesar (sudah di-unescape via json.loads)."""
    best = None
    best_len = 0
    for m in _RE_RSC_PUSH.finditer(html):
        q = m.group(1)
        if len(q) > best_len:
            best_len = len(q)
            best = q
    if not best:
        return None
    return json.loads(best)


def _balanced_json(s: str, start: int):
    """Parse JSON value starting at start (object or array)."""
    if start < 0 or start >= len(s) or s[start] not in "{[":
        return None
    opener = s[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except Exception:
                    return None
    return None


def _extract_rsc_initial_data(html: str):
    """
    Return initialData from RSC HTML.
    - /updates → list[SeriesItem]
    - / → dict with banner, updates, newSeries, completed, popular, ...
    """
    s = _decode_biggest_rsc_frame(html)
    if not s:
        return None, {"error": "no_rsc_payload"}
    key = '"initialData":'
    idx = s.find(key)
    if idx < 0:
        return None, {"error": "no_initialData"}
    # value starts at first { or [ after key
    pos = idx + len(key)
    while pos < len(s) and s[pos] in " \t\n\r":
        pos += 1
    data = _balanced_json(s, pos)
    meta: dict = {}
    m = _RE_LAST_PAGE.search(s)
    if m:
        meta["lastPage"] = int(m.group(1))
    m = _RE_PAGE.search(s)
    if m:
        meta["page"] = int(m.group(1))
    return data, meta


def _orm_to_series_item(raw: dict) -> dict | None:
    """Normalisasi item popular (bentuk Lucid/Adonis ORM) → SeriesItem standar."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("data"), dict) and raw.get("data", {}).get("slug"):
        return raw
    attrs = raw.get("$attributes") or raw.get("attributes")
    if not isinstance(attrs, dict):
        return None
    data_keys = (
        "title",
        "nativeTitle",
        "slug",
        "coverImage",
        "backgroundImage",
        "synopsis",
        "isHot",
        "author",
        "rating",
        "totalChapters",
        "releaseDate",
        "status",
        "format",
        "type",
        "genreIds",
        "genres",
        "isRecommended",
        "folderCdn",
        "animeAdaptation",
        "animeStatus",
    )
    data = {k: attrs[k] for k in data_keys if k in attrs}
    if not data.get("slug"):
        return None
    item = {
        "id": attrs.get("id") or raw.get("id"),
        "createdAt": attrs.get("createdAt"),
        "updatedAt": attrs.get("updatedAt"),
        "isDraft": attrs.get("isDraft", False),
        "data": data,
        "chapters": [],
        "metadata": {
            "views": {
                "total": attrs.get("totalViews") or attrs.get("totalViewsComputed") or 0,
            },
            "bookmarkCount": attrs.get("bookmarkCount") or 0,
            "ranking": attrs.get("ranking"),
        },
        "provider": "voratoon",
        "_source": "voratoon_home_orm",
    }
    return item


def _normalize_feed_items(items: list, *, strip_images: bool = True) -> list:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if "$attributes" in it or "modelOptions" in it:
            it = _orm_to_series_item(it)
            if not it:
                continue
        out.append(_normalize_series_item(it, strip_chapter_images=strip_images))
    return out


def fetch_updates_html(page: int = 1) -> dict[str, Any]:
    """
    Scrape https://v1.voratoon.com/updates — urutan resmi situs.
    30 item / halaman, ~345 halaman.
    """
    page = max(1, int(page or 1))
    ck = f"updates_payload:{page}"
    hit = _mem_get(ck)
    if isinstance(hit, dict) and hit.get("data") is not None:
        return hit
    url = f"{SITE_BASE}/updates" if page == 1 else f"{SITE_BASE}/updates?page={page}"
    html = _get_html(url, ttl=75.0)
    data, meta = _extract_rsc_initial_data(html)
    items = data if isinstance(data, list) else []
    out = _normalize_feed_items(items, strip_images=True)
    last = meta.get("lastPage") or 345
    result = {
        "status": 200,
        "message": "Voratoon updates (site RSC)",
        "data": out,
        "meta": {
            "source": "voratoon_updates_html",
            "page": page,
            "lastPage": last,
            "total": last * 30,
            "take": 30,
            "mode": "newest",
            "upstream": SITE_BASE,
            "per_page": len(out),
        },
    }
    _mem_set(ck, result, float(os.environ.get("VORATOON_UPDATES_TTL", "60")))
    return result


def fetch_home_rsc() -> dict[str, Any]:
    """
    Scrape https://v1.voratoon.com/ home initialData:
      banner, popular, updates, newSeries, completed, ...
    Cached ~2 minutes in-process.
    """
    hit = _mem_get("home_rsc_payload")
    if isinstance(hit, dict):
        return hit
    html = _get_html(f"{SITE_BASE}/", ttl=120)
    data, meta = _extract_rsc_initial_data(html)
    if not isinstance(data, dict):
        raise RuntimeError(f"home initialData not object: {type(data)}")
    out = {
        "banner": _normalize_feed_items(data.get("banner") or []),
        "updates": _normalize_feed_items(data.get("updates") or []),
        "newSeries": _normalize_feed_items(data.get("newSeries") or []),
        "completed": _normalize_feed_items(data.get("completed") or []),
        "popular": _normalize_feed_items(data.get("popular") or []),
        "meta": {"source": "voratoon_home_rsc", "upstream": SITE_BASE, **meta},
    }
    _mem_set("home_rsc_payload", out, 120)
    return out


def _home_feed_payload(mode: str, take: int, page: int) -> dict[str, Any] | None:
    """
    Ambil feed dari home RSC untuk tab yang tidak butuh pagination dalam.
    page>1 → None (caller fallback API).
    """
    if page > 1:
        return None
    home = fetch_home_rsc()
    key_map = {
        "new_series": "newSeries",
        "completed": "completed",
        "hot": "popular",
        "newest": "updates",  # optional short path page1
    }
    key = key_map.get(mode)
    if not key:
        return None
    items = home.get(key) or []
    if take and take < len(items):
        items = items[:take]
    return {
        "status": 200,
        "message": f"Voratoon home RSC:{key}",
        "data": items,
        "meta": {
            "source": "voratoon_home_rsc",
            "feed": key,
            "page": 1,
            "lastPage": 1 if mode != "newest" else 345,
            "total": len(items) if mode != "newest" else 10350,
            "take": take,
            "mode": mode,
            "upstream": SITE_BASE,
            "note": "Home RSC snapshot (tab curated); page>1 uses other source",
        },
    }





def get_popular(take: int = 20, page: int = 1) -> dict[str, Any]:
    """Public popular list — normalized, no ORM internals."""
    take = max(1, min(50, int(take or 20)))
    page = max(1, int(page or 1))
    items: list = []
    source = "voratoon_home_rsc"
    # Prefer home RSC popular (already normalized via _normalize_feed_items)
    try:
        if page == 1:
            home = fetch_home_rsc()
            items = list(home.get("popular") or [])
            source = "voratoon_home_rsc"
    except Exception as e:
        print("get_popular home RSC:", e, flush=True)
    # Fallback: API sort popularity
    if not items:
        try:
            payload = get_series_list(take=take, page=page, mode="hot", sort="popularity")
            items = list((payload or {}).get("data") or [])
            source = (payload or {}).get("meta", {}).get("source") or "voratoon_api_hot"
        except Exception as e:
            print("get_popular api hot:", e, flush=True)
            items = []
    # Final sanitize — strip any leftover ORM keys
    clean = []
    for it in items[:take]:
        if not isinstance(it, dict):
            continue
        # already SeriesItem shape?
        if isinstance(it.get("data"), dict) and it["data"].get("slug"):
            d = it["data"]
            clean.append({
                "id": it.get("id"),
                "createdAt": it.get("createdAt"),
                "updatedAt": it.get("updatedAt"),
                "data": {
                    "title": d.get("title"),
                    "slug": d.get("slug"),
                    "coverImage": d.get("coverImage") or d.get("cover"),
                    "backgroundImage": d.get("backgroundImage"),
                    "synopsis": d.get("synopsis"),
                    "status": d.get("status"),
                    "format": d.get("format") or d.get("type"),
                    "type": d.get("type"),
                    "rating": d.get("rating"),
                    "author": d.get("author"),
                    "totalChapters": d.get("totalChapters"),
                    "isHot": d.get("isHot"),
                    "genres": d.get("genres") or [],
                },
                "chapters": it.get("chapters") if isinstance(it.get("chapters"), list) else [],
                "metadata": {
                    "views": ((it.get("metadata") or {}).get("views") or {}),
                    "bookmarkCount": (it.get("metadata") or {}).get("bookmarkCount"),
                    "ranking": (it.get("metadata") or {}).get("ranking"),
                },
                "provider": "voratoon",
            })
            continue
        norm = _orm_to_series_item(it)
        if norm:
            d = norm.get("data") or {}
            clean.append({
                "id": norm.get("id"),
                "createdAt": norm.get("createdAt"),
                "updatedAt": norm.get("updatedAt"),
                "data": {
                    "title": d.get("title"),
                    "slug": d.get("slug"),
                    "coverImage": d.get("coverImage") or d.get("cover"),
                    "backgroundImage": d.get("backgroundImage"),
                    "synopsis": d.get("synopsis"),
                    "status": d.get("status"),
                    "format": d.get("format") or d.get("type"),
                    "type": d.get("type"),
                    "rating": d.get("rating"),
                    "author": d.get("author"),
                    "totalChapters": d.get("totalChapters"),
                    "isHot": d.get("isHot"),
                    "genres": d.get("genres") or [],
                },
                "chapters": [],
                "metadata": norm.get("metadata") or {},
                "provider": "voratoon",
            })
    return {
        "status": 200,
        "message": "Popular series",
        "data": clean,
        "meta": {
            "source": source,
            "page": page,
            "take": take,
            "total": len(clean),
            "provider": "voratoon",
        },
    }


def fetch_browse_html(
    *,
    page: int = 1,
    status: str = "",
    format_: str = "",
    type_: str = "",
    q: str = "",
    genre: str = "",
) -> dict[str, Any]:
    """Scrape /browse RSC — katalog + filter + pagination resmi situs."""
    import urllib.parse

    page = max(1, int(page or 1))
    qs = {}
    if page > 1:
        qs["page"] = str(page)
    if status:
        qs["status"] = status
    if format_:
        qs["format"] = format_
    if type_:
        qs["type"] = type_
    if q:
        qs["search"] = q  # browse page uses search query string
        qs["q"] = q
    if genre:
        gid = resolve_genre_id(genre)
        if gid:
            qs["genreIds"] = gid
        qs["genre"] = genre
    query = urllib.parse.urlencode(qs)
    url = f"{SITE_BASE}/browse" + (f"?{query}" if query else "")
    html = _get_html(url)
    data, meta = _extract_rsc_initial_data(html)
    if not isinstance(data, dict):
        raise RuntimeError("browse initialData not object")
    series_raw = data.get("series")
    # Filter query sering membuat series="$undefined" (data client-side) → fallback API
    if not isinstance(series_raw, list):
        params = {
            "take": 30,
            "page": page,
            "sort": "updatedAt",
            "sortOrder": "desc",
            "takeChapter": 2,
            "includeMeta": 1,
        }
        if status:
            params["status"] = status
        if format_:
            params["format"] = format_
        if type_:
            params["type"] = type_
        if q:
            params["title"] = q
        if genre:
            gid = resolve_genre_id(genre)
            if gid:
                params["genreIds"] = gid
            else:
                params["genre"] = genre
        api = _get("/series", params)
        items = _normalize_feed_items(api.get("data") or [], strip_images=True)
        am = api.get("meta") or {}
        sm = {
            "page": am.get("page") or page,
            "lastPage": am.get("lastPage") or 1,
            "total": am.get("total") or len(items),
        }
        data = {
            "statusQuery": status,
            "formatQuery": format_,
            "typeQuery": type_,
            "searchQuery": q,
            "genres": data.get("genres") or [],
        }
    else:
        items = _normalize_feed_items(series_raw, strip_images=True)
        sm = data.get("seriesMeta") or {}
        if not isinstance(sm, dict) or sm.get("page") is None:
            sm = {"page": page, "lastPage": page, "total": len(items)}
    # genres from browse payload
    genres_raw = data.get("genres") or []
    genres = []
    for g in genres_raw:
        if not isinstance(g, dict):
            continue
        gd = g.get("data") if isinstance(g.get("data"), dict) else g
        name = (gd.get("name") or g.get("name") or "").strip()
        if name:
            genres.append({"id": g.get("id") or gd.get("id"), "name": name, "slug": (gd.get("slug") or name.lower().replace(" ", "-"))})
    return {
        "status": 200,
        "message": "Voratoon browse (site RSC)",
        "data": items,
        "genres": genres,
        "meta": {
            "source": "voratoon_browse_html",
            "page": int(sm.get("page") or page),
            "lastPage": int(sm.get("lastPage") or page),
            "total": int(sm.get("total") or len(items)),
            "take": 30,
            "mode": "browse",
            "filters": {
                "status": data.get("statusQuery") or status or "",
                "format": data.get("formatQuery") or format_ or "",
                "type": data.get("typeQuery") or type_ or "",
                "q": data.get("searchQuery") or q or "",
                "genre": genre or "",
            },
            "upstream": SITE_BASE,
        },
    }



_genre_id_cache: dict[str, str] = {}


def resolve_genre_id(genre: str) -> str:
    """Map genre name/slug to Voratoon genreIds (API filter key)."""
    g = (genre or "").strip()
    if not g:
        return ""
    if g.isdigit():
        return g
    key = g.lower()
    if key in _genre_id_cache:
        return _genre_id_cache[key]
    try:
        payload = get_genres()
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            slug = str(item.get("slug") or "").strip()
            gid = str(item.get("id") or "").strip()
            if not gid:
                continue
            if name:
                _genre_id_cache[name.lower()] = gid
            if slug:
                _genre_id_cache[slug.lower()] = gid
    except Exception as e:
        print("resolve_genre_id:", e, flush=True)
    return _genre_id_cache.get(key, "")


def get_genres() -> dict[str, Any]:
    """GET /genres REST — 48 genre."""
    data = _get("/genres")
    rows = data.get("data") or []
    out = []
    for g in rows:
        if not isinstance(g, dict):
            continue
        gd = g.get("data") if isinstance(g.get("data"), dict) else g
        name = (gd.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "id": g.get("id"),
            "name": name,
            "slug": (gd.get("slug") or name.lower().replace(" ", "-")),
            "description": gd.get("description"),
        })
    out.sort(key=lambda x: x["name"].lower())
    return {
        "status": 200,
        "message": "genres",
        "data": out,
        "meta": {"source": "voratoon_api", "total": len(out)},
    }


def _get(path: str, params: dict | None = None, *, ttl: float = 45.0, timeout: float | None = None) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    )
    url = f"{BASE}{path}" + (f"?{q}" if q else "")
    ck = f"json:{url}"
    if ttl > 0:
        hit = _mem_get(ck)
        if isinstance(hit, dict):
            return hit
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive",
        },
        method="GET",
    )
    to = timeout if timeout is not None else TIMEOUT_LIST
    try:
        with _OPENER.open(req, timeout=to) as resp:
            body = _read_body(resp)
            data = json.loads(body.decode("utf-8", errors="replace"))
            if ttl > 0 and isinstance(data, dict):
                _mem_set(ck, data, ttl)
            return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"Voratoon HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise RuntimeError(f"Voratoon error: {e}") from e


def parse_chapter_number(value) -> int | float | None:
    """Parse 12, 12.5, 'Chapter 12', 'Ch.12-1', '12 Part 2' → number sortable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v) if v == int(v) else v
    s = str(value).strip()
    if not s:
        return None
    # direct float
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        pass
    # Chapter 12.5 / Ch. 12 / Ep 12 / 12화
    m = re.search(r"(?:chapter|ch\.?|ep\.?|episode)\s*(\d+(?:\.\d+)?)", s, re.I)
    if m:
        v = float(m.group(1))
        return int(v) if v == int(v) else v
    # 12-1 → 12.1
    m = re.search(r"\b(\d+)\s*[-–]\s*(\d+)\b", s)
    if m:
        v = float(m.group(1)) + float(m.group(2)) / 10.0
        return int(v) if v == int(v) else v
    # 12 Part 2
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:part|pt\.?)\s*(\d+)", s, re.I)
    if m:
        v = float(m.group(1)) + float(m.group(2)) / 100.0
        return v
    # first number in string
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        v = float(m.group(1))
        return int(v) if v == int(v) else v
    return None


def _chapter_index(ch: dict) -> int | float | None:
    if not isinstance(ch, dict):
        return None
    for key in ("chapterIndex", "index", "number", "chapter_number"):
        n = parse_chapter_number(ch.get(key))
        if n is not None:
            return n
    d = ch.get("data") if isinstance(ch.get("data"), dict) else {}
    for key in ("index", "chapterIndex", "number", "chapter_number", "title"):
        n = parse_chapter_number(d.get(key))
        if n is not None:
            return n
    n = parse_chapter_number(ch.get("title") or ch.get("name"))
    return n


def _normalize_chapter(ch: dict, *, strip_images: bool = False) -> dict:
    """Pastikan data.index + title; buang payload gambar berat di list."""
    if not isinstance(ch, dict):
        return ch
    out = dict(ch)
    idx = _chapter_index(out)
    data = dict(out.get("data") or {}) if isinstance(out.get("data"), dict) else {}
    if idx is not None:
        data["index"] = idx
        out["chapterIndex"] = idx
    title = data.get("title") or out.get("title")
    if not title and idx is not None:
        title = f"Chapter {idx}"
    data["title"] = title
    if strip_images:
        data.pop("images", None)
        out.pop("dataImages", None)
        data["images"] = []
    out["data"] = data
    out["provider"] = "voratoon"
    out.setdefault("createdAt", ch.get("createdAt"))
    out.setdefault("updatedAt", ch.get("updatedAt") or ch.get("createdAt"))
    return out


def _normalize_series_item(item: dict, *, strip_chapter_images: bool = True) -> dict:
    """Normalize to public SeriesItem only (strip Lucid/Adonis ORM fields)."""
    if not isinstance(item, dict):
        return item
    # Unpack ORM wrapper first
    if item.get("$attributes") or item.get("modelOptions") or item.get("$isPersisted") is not None:
        unpacked = _orm_to_series_item(item)
        if unpacked:
            item = unpacked
    # Nested data may still be missing — try attributes again
    d_in = item.get("data") if isinstance(item.get("data"), dict) else {}
    if not d_in.get("slug") and isinstance(item.get("$attributes"), dict):
        unpacked = _orm_to_series_item(item)
        if unpacked:
            item = unpacked
            d_in = item.get("data") or {}

    d_in = dict(d_in) if isinstance(d_in, dict) else {}
    data = {
        "title": d_in.get("title"),
        "nativeTitle": d_in.get("nativeTitle"),
        "slug": d_in.get("slug"),
        "coverImage": d_in.get("coverImage") or d_in.get("cover"),
        "backgroundImage": d_in.get("backgroundImage"),
        "synopsis": d_in.get("synopsis"),
        "status": d_in.get("status"),
        "format": d_in.get("format") or d_in.get("type"),
        "type": d_in.get("type"),
        "rating": d_in.get("rating"),
        "author": d_in.get("author"),
        "totalChapters": d_in.get("totalChapters"),
        "isHot": d_in.get("isHot"),
        "genres": d_in.get("genres") if isinstance(d_in.get("genres"), list) else [],
        "latestChapterLabel": d_in.get("latestChapterLabel"),
        "provider": "voratoon",
    }
    if data.get("totalChapters") is None:
        meta = item.get("dataMetadata") or item.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("totalChapters") is not None:
            data["totalChapters"] = meta.get("totalChapters")

    chs = item.get("chapters")
    norm_chs = []
    if isinstance(chs, list):
        for c in chs:
            if isinstance(c, dict):
                norm_chs.append(_normalize_chapter(c, strip_images=strip_chapter_images))
        if norm_chs and not data.get("latestChapterLabel"):
            idx = _chapter_index(norm_chs[0])
            if idx is not None:
                data["latestChapterLabel"] = f"Chapter {idx}"

    meta_out = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    views = meta_out.get("views") if isinstance(meta_out.get("views"), dict) else {}
    return {
        "id": item.get("id"),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "data": data,
        "chapters": norm_chs,
        "metadata": {
            "views": views,
            "bookmarkCount": meta_out.get("bookmarkCount"),
            "ranking": meta_out.get("ranking"),
        },
        "provider": "voratoon",
        "_source": item.get("_source") or "voratoon",
    }




def _parse_iso(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        from datetime import datetime
        s2 = str(s).replace("Z", "+00:00")
        return datetime.fromisoformat(s2).timestamp()
    except Exception:
        return 0.0


def _as_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _latest_chapter_ts(it: dict) -> float:
    best = 0.0
    for ch in it.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        best = max(best, _parse_iso(ch.get("createdAt") or ch.get("updatedAt")))
    return best


def get_series_list(
    *,
    take: int = 30,
    page: int = 1,
    sort: str = "updatedAt",
    sort_order: str = "desc",
    q: str = "",
    status: str = "",
    format_: str = "",
    take_chapter: int = 3,
    mode: str = "newest",
    type_: str = "",
    genre: str = "",
) -> dict[str, Any]:
    """
    mode:
      newest     — chapter update ≤21 hari (bukan katalog import)
      new_series — series baru (series.createdAt ≤45 hari)
      completed  — status=completed
      hot        — popularity
      project    — type=project
      search     — text search
    """
    import time

    now = time.time()
    mode = (mode or "newest").lower().strip()
    if sort in ("popular", "hot", "views", "popularity"):
        mode = "hot"
    if q:
        mode = "search"

    # Feed resmi dari situs (RSC) — prioritas tertinggi
    # Genre filter: REST genreIds lebih andal daripada RSC browse
    if genre and mode in ("browse", "search", "newest", ""):
        mode = "browse"
    if mode == "browse" or (mode == "search" and (status or format_ or type_)):
        try:
            payload = fetch_browse_html(
                page=page,
                status=status,
                format_=format_,
                type_=type_,
                q=q,
                genre=genre,
            )
            items = payload.get("data") or []
            # if genre requested, prefer API genreIds (RSC often ignores genre=)
            if genre:
                gid = resolve_genre_id(genre)
                if gid:
                    try:
                        api = _get(
                            "/series",
                            {
                                "take": take or 30,
                                "page": page,
                                "sort": "updatedAt",
                                "sortOrder": "desc",
                                "takeChapter": 2,
                                "includeMeta": 1,
                                "genreIds": gid,
                            },
                            ttl=60,
                        )
                        api_items = [
                            _normalize_series_item(it, strip_chapter_images=True)
                            for it in (api.get("data") or [])
                            if isinstance(it, dict)
                        ]
                        if api_items:
                            am = api.get("meta") or {}
                            return {
                                "status": 200,
                                "message": f"Voratoon genre:{genre}",
                                "data": api_items[: take or 30],
                                "meta": {
                                    "source": "voratoon_api_genreIds",
                                    "page": am.get("page") or page,
                                    "lastPage": am.get("lastPage") or 1,
                                    "total": am.get("total") or len(api_items),
                                    "mode": "browse",
                                    "filters": {"genre": genre, "genreIds": gid},
                                    "provider": "voratoon",
                                },
                            }
                    except Exception as ge:
                        print("voratoon genreIds api fail:", ge, flush=True)
            if take and take < len(items):
                payload["data"] = items[:take]
            return payload
        except Exception as e:
            print("voratoon browse RSC fail:", e, flush=True)

    if not q and mode in ("newest", "new_series", "completed", "hot"):
        try:
            if mode == "newest":
                payload = fetch_updates_html(page=page)
                items = payload.get("data") or []
                if take and take < len(items):
                    items = items[:take]
                    payload["data"] = items
                return payload
            payload = _home_feed_payload(mode, take=take, page=page)
            if payload and payload.get("data"):
                return payload
        except Exception as e:
            print(f"voratoon RSC feed fail mode={mode}:", e, flush=True)

    # Upstream query
    api_sort = "popularity" if mode == "hot" else "updatedAt"
    if mode == "new_series":
        api_sort = "createdAt"
    params: dict[str, Any] = {
        "take": 40 if mode in ("newest", "new_series") else take,
        "page": page if mode not in ("newest", "new_series") else 1,
        "sort": api_sort,
        "sortOrder": sort_order or "desc",
        "takeChapter": max(1, min(take_chapter or 3, 5)),
        "includeMeta": 1,
    }
    if mode == "completed":
        params["status"] = "completed"
        params["take"] = take
        params["page"] = page
    elif mode == "hot":
        params["sort"] = "popularity"
        params["take"] = take
        params["page"] = page
    elif mode == "project":
        params["type"] = "project"
        params["take"] = take
        params["page"] = page
    elif status:
        params["status"] = status
    if format_:
        params["format"] = format_
    if genre:
        gid = resolve_genre_id(genre)
        if gid:
            params["genreIds"] = gid
        else:
            params["genre"] = genre
    if type_ and mode != "project":
        params["type"] = type_
    if q:
        # Voratoon: hanya `title=` yang relevan; search/q diabaikan API
        params["title"] = q
        params["take"] = take
        params["page"] = page
        params["sort"] = "updatedAt"

    # Pool pages for newest / new_series (parallel) — else single request
    raw_items: list[dict] = []
    meta: dict = {}
    pages_to_fetch = 2 if mode in ("newest", "new_series") else 1

    def _fetch_page(pg: int) -> tuple[int, list, dict]:
        p = dict(params)
        if mode in ("newest", "new_series"):
            p["page"] = pg
            p["take"] = 40
        data = _get("/series", p, ttl=40, timeout=TIMEOUT_LIST)
        batch = [
            _normalize_series_item(it, strip_chapter_images=True)
            for it in (data.get("data") or [])
            if isinstance(it, dict)
        ]
        return pg, batch, (data.get("meta") or {})

    if pages_to_fetch == 1:
        try:
            _, batch, meta = _fetch_page(max(1, page))
            raw_items = batch
        except Exception as e:
            print("voratoon list page fail:", e, flush=True)
            raw_items = []
            meta = {}
    else:
        # Parallel fetch page 1..N then merge in order
        by_page: dict[int, list] = {}
        futs = [_POOL.submit(_fetch_page, pg) for pg in range(1, pages_to_fetch + 1)]
        for fut in as_completed(futs):
            try:
                pg, batch, m = fut.result()
                by_page[pg] = batch
                if pg == 1:
                    meta = m or meta
            except Exception as e:
                print("voratoon parallel page fail:", e, flush=True)
        for pg in sorted(by_page.keys()):
            raw_items.extend(by_page[pg])

    def enrich(it: dict) -> dict:
        d = it.get("data") if isinstance(it.get("data"), dict) else {}
        chs = it.get("chapters") or []
        if chs and isinstance(chs[0], dict):
            idx = _chapter_index(chs[0])
            if idx is not None:
                d["latestChapterLabel"] = f"Chapter {idx}"
            d["updatedLabel"] = chs[0].get("createdAt") or it.get("updatedAt")
        it["data"] = d
        return it

    items = raw_items

    if mode == "newest":
        # Hanya update chapter ≤ 21 hari; ranking by chapter time
        max_age = 21 * 86400
        scored = []
        for it in items:
            ch_ts = _latest_chapter_ts(it)
            if not ch_ts or (now - ch_ts) > max_age:
                continue
            d = it.get("data") or {}
            tc = _as_int(d.get("totalChapters"), 0)
            # Boost ongoing series with real chapter depth
            score = ch_ts + min(tc, 50) * 60  # up to +50min equivalent
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        # dedupe by slug
        seen = set()
        ranked = []
        for _, it in scored:
            slug = ((it.get("data") or {}).get("slug") or "").lower()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            ranked.append(enrich(it))
        # paginate local
        start = (max(1, page) - 1) * take
        items = ranked[start : start + take]
        meta = {
            "page": page,
            "lastPage": max(1, (len(ranked) + take - 1) // take),
            "total": len(ranked),
            "mode": "newest",
            "window_days": 21,
        }
    elif mode == "new_series":
        max_age = 45 * 86400
        scored = []
        for it in items:
            ser_ts = _parse_iso(it.get("createdAt"))
            if not ser_ts or (now - ser_ts) > max_age:
                continue
            d = it.get("data") or {}
            tc = _as_int(d.get("totalChapters"), 0)
            # Prefer truly new (few chapters) slightly
            score = ser_ts - min(tc, 20) * 3600
            scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        ranked = []
        for _, it in scored:
            slug = ((it.get("data") or {}).get("slug") or "").lower()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            ranked.append(enrich(it))
        start = (max(1, page) - 1) * take
        items = ranked[start : start + take]
        meta = {
            "page": page,
            "lastPage": max(1, (len(ranked) + take - 1) // take),
            "total": len(ranked),
            "mode": "new_series",
            "window_days": 45,
        }
    elif mode == "completed":
        # Prefer longer completed series
        scored = []
        for it in items:
            d = it.get("data") or {}
            tc = _as_int(d.get("totalChapters"), 0)
            ts = _latest_chapter_ts(it) or _parse_iso(it.get("updatedAt"))
            scored.append((tc * 1000 + ts / 1e6, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        items = [enrich(it) for _, it in scored]
        meta = {
            "page": meta.get("page") or page,
            "lastPage": meta.get("lastPage") or 1,
            "total": meta.get("total") or len(items),
            "mode": "completed",
        }
    else:
        items = [enrich(it) for it in items[:take] if isinstance(it, dict)]
        meta = {
            "page": meta.get("page") or page,
            "lastPage": meta.get("lastPage") or 1,
            "total": meta.get("total") or len(items),
            "mode": mode,
        }

    return {
        "status": 200,
        "message": f"Voratoon {mode}",
        "data": items,
        "meta": {
            "source": "voratoon",
            "page": meta.get("page") or page,
            "lastPage": meta.get("lastPage") or 1,
            "total": meta.get("total") or len(items),
            "take": take,
            "mode": mode,
            "upstream": BASE,
            **{k: v for k, v in meta.items() if k in ("window_days",)},
        },
    }



def get_series_detail(slug: str) -> dict[str, Any] | None:
    slug = (slug or "").strip()
    if not slug:
        return None
    data = _get(
        "/series",
        {
            "take": 1,
            "page": 1,
            "includeMeta": 1,
            "takeChapter": 3,
            "filter": f"slug=={slug}",
        },
    )
    items = data.get("data") or []
    if not items:
        try:
            data = _get(f"/series/{urllib.parse.quote(slug)}")
            item = data.get("data")
            if isinstance(item, list):
                items = item
            elif isinstance(item, dict):
                items = [item]
        except Exception:
            pass
    if not items:
        return None
    item = _normalize_series_item(items[0], strip_chapter_images=True)
    # attach full chapter count if only preview present
    d = item.get("data") or {}
    try:
        ch_payload = get_chapters(slug)
        n = len(ch_payload.get("data") or [])
        if n and isinstance(d, dict):
            d = dict(d)
            d["totalChapters"] = n
            item["data"] = d
            # keep preview only on item.chapters from list; full list via /chapters
    except Exception:
        pass
    return {
        "status": 200,
        "message": "Voratoon detail",
        "data": item,
        "meta": {"source": "voratoon", "upstream": BASE},
    }


def get_chapters(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip()
    data = _get(
        f"/series/{urllib.parse.quote(slug)}/chapters",
        ttl=float(os.environ.get("VORATOON_CHAPTERS_TTL", "120")),
        timeout=TIMEOUT_LIST,
    )
    rows = [
        _normalize_chapter(ch, strip_images=True)
        for ch in (data.get("data") or [])
        if isinstance(ch, dict)
    ]
    # sort desc by index
    def _key(c):
        i = _chapter_index(c)
        return float(i) if i is not None else -1

    rows.sort(key=_key, reverse=True)
    return {
        "status": 200,
        "message": data.get("message") or "chapters",
        "data": rows,
        "meta": {"source": "voratoon", "total": len(rows), "upstream": BASE},
    }


def _images_from_payload(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    images = inner.get("images") or payload.get("images") or []
    if isinstance(images, dict):
        def sk(k):
            try:
                return int(k)
            except Exception:
                return 0
        images = [images[k] for k in sorted(images.keys(), key=sk)]
    if (not images) and isinstance(payload.get("dataImages"), dict):
        di = payload["dataImages"]
        def sk(k):
            try:
                return int(k)
            except Exception:
                return 0
        images = [di[k] for k in sorted(di.keys(), key=sk)]
    return [u for u in images if isinstance(u, str) and u.startswith("http")]


def get_pages(slug: str, chapter: str | int) -> dict[str, Any]:
    slug = (slug or "").strip()
    ref = str(chapter)
    # Chapter pages change rarely — cache longer (5 min)
    data = _get(
        f"/series/{urllib.parse.quote(slug)}/chapters/{urllib.parse.quote(ref)}",
        ttl=float(os.environ.get("VORATOON_PAGES_TTL", "300")),
        timeout=float(os.environ.get("VORATOON_TIMEOUT_PAGES", "10")),
    )
    payload = data.get("data") or {}
    if not isinstance(payload, dict):
        return {
            "status": 200,
            "message": "ok",
            "data": {"data": {"images": []}, "chapterIndex": ref},
            "meta": {"source": "voratoon"},
        }
    images = _images_from_payload(payload)
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    idx = payload.get("chapterIndex") or _chapter_index(payload) or ref
    try:
        idx_n = float(idx)
        idx = int(idx_n) if idx_n == int(idx_n) else idx_n
    except Exception:
        pass
    title = (inner or {}).get("title") or payload.get("title") or f"Chapter {idx}"
    shaped = {
        "id": payload.get("id"),
        "createdAt": payload.get("createdAt"),
        "updatedAt": payload.get("updatedAt"),
        "chapterIndex": idx,
        "data": {
            "index": idx,
            "title": title,
            "images": images,
            "slug": (inner or {}).get("slug"),
        },
        "provider": "voratoon",
    }
    return {
        "status": 200,
        "message": "ok",
        "data": shaped,
        "meta": {
            "source": "voratoon",
            "provider": "voratoon",
            "total_images": len(images),
        },
    }


def search(q: str, limit: int = 20) -> dict[str, Any]:
    return get_series_list(take=limit, page=1, q=q, mode="search")
