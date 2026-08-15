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


def _extract_rsc_initial_data(html: str) -> tuple[list, dict]:
    """Parse Next.js RSC payload: initialData array from /updates page."""
    import re

    big = None
    for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        if len(m.group(1)) > 50000:
            big = m.group(1)
            break
    if not big:
        return [], {"error": "no_rsc_payload"}
    s = json.loads(big)
    key = '"initialData":'
    idx = s.find(key)
    if idx < 0:
        return [], {"error": "no_initialData"}
    arr_start = s.find("[", idx)
    depth = 0
    in_str = False
    esc = False
    end = None
    for i in range(arr_start, len(s)):
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
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return [], {"error": "unbalanced_array"}
    data = json.loads(s[arr_start:end])
    meta: dict = {}
    m = re.search(r'"lastPage"\s*:\s*(\d+)', s)
    if m:
        meta["lastPage"] = int(m.group(1))
    m = re.search(r'"page"\s*:\s*(\d+)', s)
    if m:
        meta["page"] = int(m.group(1))
    return data if isinstance(data, list) else [], meta


def fetch_updates_html(page: int = 1) -> dict[str, Any]:
    """
    Scrape https://v1.voratoon.com/updates — urutan resmi situs (bukan API sort).
    30 item / halaman, ~345 halaman.
    """
    page = max(1, int(page or 1))
    url = f"{SITE_BASE}/updates" if page == 1 else f"{SITE_BASE}/updates?page={page}"
    html = _get_html(url)
    items, meta = _extract_rsc_initial_data(html)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # Strip heavy images from preview chapters
        norm = _normalize_series_item(it, strip_chapter_images=True)
        out.append(norm)
    last = meta.get("lastPage") or 345
    return {
        "status": 200,
        "message": "Voratoon updates (site)",
        "data": out,
        "meta": {
            "source": "voratoon_updates_html",
            "page": page,
            "lastPage": last,
            "total": last * 30,  # approximate; site shows ~30/page
            "take": 30,
            "mode": "newest",
            "upstream": SITE_BASE,
            "per_page": len(out),
        },
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


def _chapter_index(ch: dict) -> int | float | None:
    if not isinstance(ch, dict):
        return None
    for key in ("chapterIndex", "index", "number"):
        if ch.get(key) is not None:
            try:
                v = float(ch[key])
                return int(v) if v == int(v) else v
            except (TypeError, ValueError):
                pass
    d = ch.get("data") if isinstance(ch.get("data"), dict) else {}
    for key in ("index", "chapterIndex", "number"):
        if d.get(key) is not None:
            try:
                v = float(d[key])
                return int(v) if v == int(v) else v
            except (TypeError, ValueError):
                pass
    return None


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

    # TERBARU: pakai urutan resmi situs /updates (HTML RSC), bukan API sort
    if mode == "newest" and not q:
        try:
            payload = fetch_updates_html(page=page)
            # optional slice if take < 30
            items = payload.get("data") or []
            if take and take < len(items):
                items = items[:take]
                payload["data"] = items
            return payload
        except Exception as e:
            # fallback ke API ranking di bawah
            print("voratoon updates html fail:", e, flush=True)

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
    if type_ and mode != "project":
        params["type"] = type_
    if q:
        params["search"] = q
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
    return get_series_list(take=limit, page=1, q=q)
