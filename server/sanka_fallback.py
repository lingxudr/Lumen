"""
Fallback sementara saat Komikcast down — REST Sanka Vollerei.

Base: https://www.sankavollerei.web.id

OK:
  GET /comic/terbaru
  GET /comic/populer
  GET /comic/search?q=
  GET /comic/chapter/{slug}-chapter-{n}
  GET /comic/chapter/{slug}/chapter-{n}

Detail/chapter-list sering di-block Plana AI → pakai Komiku HTML bila perlu.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SANKA_BASE = "https://www.sankavollerei.web.id"
UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
)


def _get_json(path: str, timeout: int = 18) -> dict[str, Any]:
    url = SANKA_BASE + path
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": SANKA_BASE + "/comic",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}
        raise RuntimeError(
            f"Sanka HTTP {e.code}: {data.get('message') or body[:120]}"
        ) from e
    data = json.loads(body)
    if isinstance(data, dict) and data.get("status") == "Plana AI Detector":
        raise RuntimeError(data.get("message") or "Plana AI block")
    return data


def _slug_from_link(link: str | None) -> str:
    if not link:
        return ""
    # /manga/foo/ or https://komiku.org/manga/foo/
    parts = link.rstrip("/").split("/")
    return parts[-1] if parts else ""


def _chapter_num(label: str | None) -> float | None:
    if not label:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", label)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def to_series_item(comic: dict[str, Any]) -> dict[str, Any]:
    """Map item Sanka → bentuk series Komikcast (frontend Lumen)."""
    title = comic.get("title") or ""
    link = comic.get("link") or comic.get("href") or ""
    slug = comic.get("slug") or _slug_from_link(link)
    image = comic.get("image") or comic.get("thumbnail") or ""
    ch_label = comic.get("chapter") or ""
    time_ago = comic.get("time_ago") or comic.get("description") or ""
    ch_num = _chapter_num(ch_label)
    chapters = []
    if ch_label:
        chapters.append(
            {
                "id": None,
                "createdAt": None,
                "data": {
                    "index": int(ch_num)
                    if ch_num is not None and float(ch_num).is_integer()
                    else ch_num,
                    "title": ch_label,
                    "slug": f"{slug}-chapter-{int(ch_num)}"
                    if ch_num is not None and float(ch_num).is_integer()
                    else None,
                },
                "provider": "sanka",
                "updated_label": time_ago,
            }
        )
    fmt = (comic.get("type") or "").lower() or None
    return {
        "id": slug,
        "data": {
            "title": title,
            "nativeTitle": comic.get("altTitle"),
            "slug": slug,
            "coverImage": image,
            "status": None,
            "format": fmt,
            "type": "mirror",
            "genreIds": [],
            "isHot": False,
            "totalChapters": int(ch_num)
            if ch_num is not None and float(ch_num).is_integer()
            else ch_num,
            "provider": "sanka",
            "latestChapterLabel": ch_label or None,
            "updatedLabel": time_ago or None,
        },
        "createdAt": None,
        "updatedAt": time_ago,
        "chapters": chapters,
        "provider": "sanka",
        "_source": "sanka",
    }


def get_terbaru(limit: int = 20) -> dict[str, Any]:
    data = _get_json("/comic/terbaru")
    comics = data.get("comics") or []
    items = [to_series_item(c) for c in comics[:limit] if isinstance(c, dict)]
    return {
        "status": 200,
        "message": "Sanka fallback terbaru (Komikcast down)",
        "data": items,
        "meta": {
            "source": "sanka",
            "total": len(items),
            "creator": data.get("creator"),
            "stale": False,
        },
    }


def get_populer(limit: int = 20) -> dict[str, Any]:
    data = _get_json("/comic/populer")
    comics = data.get("comics") or []
    items = [to_series_item(c) for c in comics[:limit] if isinstance(c, dict)]
    return {
        "status": 200,
        "message": "Sanka fallback populer",
        "data": items,
        "meta": {
            "source": "sanka",
            "total": len(items),
            "creator": data.get("creator"),
        },
    }


def search(q: str, limit: int = 20) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"status": 200, "data": [], "meta": {"source": "sanka"}}
    data = _get_json("/comic/search?q=" + urllib.parse.quote(q))
    rows = data.get("data") or []
    items = []
    for c in rows[:limit]:
        if not isinstance(c, dict):
            continue
        # normalize search shape
        c2 = {
            "title": c.get("title"),
            "slug": c.get("slug"),
            "link": c.get("href") or c.get("link"),
            "image": c.get("thumbnail") or c.get("image"),
            "type": c.get("type"),
            "chapter": None,
            "time_ago": c.get("description"),
            "altTitle": c.get("altTitle"),
        }
        items.append(to_series_item(c2))
    return {
        "status": 200,
        "message": data.get("message") or "Sanka search",
        "data": items,
        "meta": {
            "source": "sanka",
            "total": len(items),
            "q": q,
            "creator": data.get("creator"),
        },
    }


def get_chapter_images(slug: str, number: float | int | str) -> dict[str, Any]:
    """
    Ambil gambar chapter.
    Path: /comic/chapter/{slug}-chapter-{n}
    """
    slug = (slug or "").strip().strip("/")
    try:
        num_f = float(number)
        num_s = str(int(num_f)) if float(num_f).is_integer() else str(num_f)
    except (TypeError, ValueError):
        num_s = str(number)

    paths = [
        f"/comic/chapter/{slug}-chapter-{num_s}",
        f"/comic/chapter/{slug}/chapter-{num_s}",
    ]
    last_err = None
    data = None
    for p in paths:
        try:
            data = _get_json(p)
            break
        except Exception as e:
            last_err = e
            continue
    if data is None:
        raise RuntimeError(str(last_err) or "chapter fetch failed")

    images = data.get("images") or []
    # skip watermark cover if obvious
    cleaned = []
    for u in images:
        if not isinstance(u, str):
            continue
        low = u.lower()
        if "wmkomiku" in low or "wm-komiku" in low:
            continue
        cleaned.append(u)

    proxies = data.get("imagesproxy") or []
    # prefer direct images; proxy as secondary
    return {
        "status": 200,
        "message": "Sanka chapter pages",
        "data": {
            "images": cleaned or [u for u in images if isinstance(u, str)],
            "images_proxy": proxies,
            "manga_title": data.get("manga_title"),
            "chapter_title": data.get("chapter_title"),
            "navigation": data.get("navigation"),
            "provider": "sanka",
            "page_count": len(cleaned or images),
        },
        "meta": {"source": "sanka", "slug": slug, "number": num_s},
    }
