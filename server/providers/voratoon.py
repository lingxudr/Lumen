"""
Voratoon API client (api.voratoon.com) — pengganti Komiku saat KC down.
Struktur response hampir identik dengan be.komikcast.cc / Lumen frontend.
Sumber: analisis paket KOMA (cek.zip).
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
TIMEOUT = float(os.environ.get("VORATOON_TIMEOUT", "14"))


def _get(path: str, params: dict | None = None) -> dict[str, Any]:
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and v != ""})
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


def get_series_list(
    *,
    take: int = 30,
    page: int = 1,
    sort: str = "updatedAt",
    sort_order: str = "desc",
    q: str = "",
    status: str = "",
    format_: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "take": take,
        "page": page,
        "sort": sort if sort not in ("popular", "hot", "views") else "popularity",
        "sortOrder": sort_order,
    }
    if sort in ("popular", "hot", "views", "popularity"):
        params["sort"] = "popularity"
    if q:
        params["search"] = q
        # some APIs use title=
        params["title"] = q
    if status:
        params["status"] = status
    if format_:
        params["format"] = format_
    data = _get("/series", params)
    # normalize meta for Lumen
    meta = data.get("meta") or {}
    items = data.get("data") or []
    # tag provider
    for it in items:
        if isinstance(it, dict):
            it.setdefault("provider", "voratoon")
            it.setdefault("_source", "voratoon")
            d = it.get("data")
            if isinstance(d, dict):
                d.setdefault("provider", "voratoon")
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
    # filter by slug
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
        # try path style if supported
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
    item = items[0]
    if isinstance(item, dict):
        item.setdefault("provider", "voratoon")
        item.setdefault("_source", "voratoon")
        d = item.get("data")
        if isinstance(d, dict):
            d.setdefault("provider", "voratoon")
    return {
        "status": 200,
        "message": "Voratoon detail",
        "data": item,
        "meta": {"source": "voratoon", "upstream": BASE},
    }


def get_chapters(slug: str) -> dict[str, Any]:
    slug = (slug or "").strip()
    data = _get(f"/series/{urllib.parse.quote(slug)}/chapters")
    rows = data.get("data") or []
    for ch in rows:
        if isinstance(ch, dict):
            ch.setdefault("provider", "voratoon")
    return {
        "status": 200,
        "message": data.get("message") or "chapters",
        "data": rows,
        "meta": {"source": "voratoon", "total": len(rows), "upstream": BASE},
    }


def get_pages(slug: str, chapter: str | int) -> dict[str, Any]:
    slug = (slug or "").strip()
    ref = str(chapter)
    data = _get(f"/series/{urllib.parse.quote(slug)}/chapters/{urllib.parse.quote(ref)}")
    payload = data.get("data") or {}
    # Ensure nested shape frontend expects: data.data.images
    if isinstance(payload, dict):
        inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        images = inner.get("images") or payload.get("images") or []
        if isinstance(images, dict):
            # map index->url
            images = [images[k] for k in sorted(images.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)]
        idx = payload.get("chapterIndex") or inner.get("index") or ref
        shaped = {
            "id": payload.get("id"),
            "createdAt": payload.get("createdAt"),
            "updatedAt": payload.get("updatedAt"),
            "chapterIndex": idx,
            "data": {
                "index": idx,
                "title": inner.get("title") or payload.get("title") or f"Chapter {idx}",
                "images": images,
                "slug": inner.get("slug"),
            },
            "dataImages": {str(i): u for i, u in enumerate(images)} if images else payload.get("dataImages"),
            "provider": "voratoon",
        }
        return {
            "status": 200,
            "message": "ok",
            "data": shaped,
            "meta": {"source": "voratoon", "provider": "voratoon", "total_images": len(images)},
        }
    return {
        "status": 200,
        "message": "ok",
        "data": payload,
        "meta": {"source": "voratoon"},
    }


def search(q: str, limit: int = 20) -> dict[str, Any]:
    return get_series_list(take=limit, page=1, q=q)
