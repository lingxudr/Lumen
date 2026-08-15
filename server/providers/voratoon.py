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
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "take": take,
        "page": page,
        "sort": sort,
        "sortOrder": sort_order,
        "takeChapter": max(0, min(take_chapter, 5)),
        "includeMeta": 1,
    }
    if sort in ("popular", "hot", "views", "popularity"):
        params["sort"] = "popularity"
    if q:
        params["search"] = q
        params["title"] = q
    if status:
        params["status"] = status
    if format_:
        params["format"] = format_
    data = _get("/series", params)
    meta = data.get("meta") or {}
    items = [
        _normalize_series_item(it, strip_chapter_images=True)
        for it in (data.get("data") or [])
        if isinstance(it, dict)
    ]
    return {
        "status": 200,
        "message": data.get("message") or "Voratoon series",
        "data": items,
        "meta": {
            "source": "voratoon",
            "page": meta.get("page") or page,
            "lastPage": meta.get("lastPage") or meta.get("totalPages") or 1,
            "total": meta.get("total") or len(items),
            "take": take,
            "upstream": BASE,
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
