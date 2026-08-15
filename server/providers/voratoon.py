"""
Voratoon API client (api.voratoon.com) — fallback utama saat Komikcast down.
Struktur response kompatibel frontend Lumen (mirip be.komikcast.cc).
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

TIMEOUT = float(os.environ.get("VORATOON_TIMEOUT", "16"))
SITE_BASE = (os.environ.get("VORATOON_SITE") or "https://v1.voratoon.com").rstrip("/")


def _get_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")



def _decode_biggest_rsc_frame(html: str) -> str | None:
    """Ambil string Flight frame terbesar (sudah di-unescape via json.loads)."""
    import re

    best = None
    best_len = 0
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
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
    import re

    m = re.search(r'"lastPage"\s*:\s*(\d+)', s)
    if m:
        meta["lastPage"] = int(m.group(1))
    m = re.search(r'"page"\s*:\s*(\d+)', s)
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
    url = f"{SITE_BASE}/updates" if page == 1 else f"{SITE_BASE}/updates?page={page}"
    html = _get_html(url)
    data, meta = _extract_rsc_initial_data(html)
    items = data if isinstance(data, list) else []
    out = _normalize_feed_items(items, strip_images=True)
    last = meta.get("lastPage") or 345
    return {
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


def fetch_home_rsc() -> dict[str, Any]:
    """
    Scrape https://v1.voratoon.com/ home initialData:
      banner, popular, updates, newSeries, completed, ...
    """
    html = _get_html(f"{SITE_BASE}/")
    data, meta = _extract_rsc_initial_data(html)
    if not isinstance(data, dict):
        raise RuntimeError(f"home initialData not object: {type(data)}")
    return {
        "banner": _normalize_feed_items(data.get("banner") or []),
        "updates": _normalize_feed_items(data.get("updates") or []),
        "newSeries": _normalize_feed_items(data.get("newSeries") or []),
        "completed": _normalize_feed_items(data.get("completed") or []),
        "popular": _normalize_feed_items(data.get("popular") or []),
        "meta": {"source": "voratoon_home_rsc", "upstream": SITE_BASE, **meta},
    }


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


def _get(path: str, params: dict | None = None) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    )
    url = f"{BASE}{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8", errors="replace"))
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
    if not isinstance(item, dict):
        return item
    out = dict(item)
    d = dict(out.get("data") or {}) if isinstance(out.get("data"), dict) else {}
    d.setdefault("provider", "voratoon")
    # totalChapters from metadata if present
    if d.get("totalChapters") is None:
        meta = out.get("dataMetadata") or out.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("totalChapters") is not None:
            d["totalChapters"] = meta.get("totalChapters")
    chs = out.get("chapters")
    if isinstance(chs, list):
        norm_chs = [
            _normalize_chapter(c, strip_images=strip_chapter_images)
            for c in chs
            if isinstance(c, dict)
        ]
        out["chapters"] = norm_chs
        if norm_chs and not d.get("latestChapterLabel"):
            top = norm_chs[0]
            idx = _chapter_index(top)
            if idx is not None:
                d["latestChapterLabel"] = f"Chapter {idx}"
                d.setdefault("totalChapters", d.get("totalChapters"))
    out["data"] = d
    out["provider"] = "voratoon"
    out["_source"] = "voratoon"
    return out




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
        params["genre"] = genre
    if type_ and mode != "project":
        params["type"] = type_
    if q:
        # Voratoon: hanya `title=` yang relevan; search/q diabaikan API
        params["title"] = q
        params["take"] = take
        params["page"] = page
        params["sort"] = "updatedAt"

    # Pool several pages for newest / new_series quality filter
    raw_items: list[dict] = []
    pages_to_fetch = 3 if mode in ("newest", "new_series") else 1
    for pg in range(1, pages_to_fetch + 1):
        p = dict(params)
        if mode in ("newest", "new_series"):
            p["page"] = pg
            p["take"] = 40
        data = _get("/series", p)
        batch = [
            _normalize_series_item(it, strip_chapter_images=True)
            for it in (data.get("data") or [])
            if isinstance(it, dict)
        ]
        raw_items.extend(batch)
        if mode not in ("newest", "new_series"):
            meta = data.get("meta") or {}
            break
        if len(batch) < 20:
            break
    else:
        data = data if raw_items else {"meta": {}}
        meta = data.get("meta") or {}

    if mode not in ("newest", "new_series"):
        data = _get("/series", params) if not raw_items else {"data": raw_items, "meta": meta}
        if not raw_items:
            raw_items = [
                _normalize_series_item(it, strip_chapter_images=True)
                for it in (data.get("data") or [])
                if isinstance(it, dict)
            ]
        meta = data.get("meta") or {}

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
    data = _get(f"/series/{urllib.parse.quote(slug)}/chapters")
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
    data = _get(
        f"/series/{urllib.parse.quote(slug)}/chapters/{urllib.parse.quote(ref)}"
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
