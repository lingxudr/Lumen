"""
Fallback saat Komikcast down — REST Sanka Vollerei.
Base: https://www.sankavollerei.web.id

OK:
  /comic/terbaru, /comic/populer, /comic/search?q=, /comic/chapter/{slug}-chapter-{n}
  /comic/shinigami/latest, /popular, /detail/{id}, /chapters/{id}, /read/{chapter_id}
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


def _get_json(path: str, timeout: int = 20) -> dict[str, Any]:
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
    return link.rstrip("/").split("/")[-1]


def _chapter_num(label: str | None) -> float | None:
    if not label:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(label))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def looks_like_uuid(s: str) -> bool:
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            (s or "").lower(),
        )
    )


def _komiku_item(comic: dict[str, Any]) -> dict[str, Any]:
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
                "data": {
                    "index": int(ch_num)
                    if ch_num is not None and float(ch_num).is_integer()
                    else ch_num,
                    "title": ch_label,
                    "slug": None,
                },
                "provider": "sanka",
            }
        )
    return {
        "id": slug,
        "data": {
            "title": title,
            "nativeTitle": comic.get("altTitle"),
            "slug": slug,
            "coverImage": image,
            "status": None,
            "format": (comic.get("type") or "").lower() or None,
            "type": "mirror",
            "totalChapters": int(ch_num)
            if ch_num is not None and float(ch_num).is_integer()
            else ch_num,
            "provider": "sanka",
            "latestChapterLabel": ch_label or None,
            "updatedLabel": time_ago or None,
        },
        "chapters": chapters,
        "provider": "sanka",
        "_source": "sanka_komiku",
    }


def get_terbaru_komiku(limit: int = 20) -> dict[str, Any]:
    data = _get_json("/comic/terbaru")
    comics = data.get("comics") or []
    items = [_komiku_item(c) for c in comics[:limit] if isinstance(c, dict)]
    return {
        "status": 200,
        "message": "Sanka Komiku terbaru (KC down)",
        "data": items,
        "meta": {"source": "sanka_komiku", "total": len(items)},
    }


def search_komiku(q: str, limit: int = 20) -> dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"status": 200, "data": [], "meta": {"source": "sanka_komiku"}}
    data = _get_json("/comic/search?q=" + urllib.parse.quote(q))
    rows = data.get("data") or []
    items = []
    for c in rows[:limit]:
        if not isinstance(c, dict):
            continue
        items.append(
            _komiku_item(
                {
                    "title": c.get("title"),
                    "slug": c.get("slug"),
                    "link": c.get("href") or c.get("link"),
                    "image": c.get("thumbnail") or c.get("image"),
                    "type": c.get("type"),
                    "time_ago": c.get("description"),
                    "altTitle": c.get("altTitle"),
                }
            )
        )
    return {
        "status": 200,
        "message": data.get("message") or "search",
        "data": items,
        "meta": {"source": "sanka_komiku", "total": len(items), "q": q},
    }


def get_chapter_images_komiku(slug: str, number) -> dict[str, Any]:
    slug = (slug or "").strip().strip("/")
    try:
        num_f = float(number)
        num_s = str(int(num_f)) if float(num_f).is_integer() else str(num_f)
    except (TypeError, ValueError):
        num_s = str(number)
    data = None
    last_err = None
    for p in (
        f"/comic/chapter/{slug}-chapter-{num_s}",
        f"/comic/chapter/{slug}/chapter-{num_s}",
    ):
        try:
            data = _get_json(p)
            break
        except Exception as e:
            last_err = e
    if data is None:
        raise RuntimeError(str(last_err) or "chapter failed")
    images = [
        u
        for u in (data.get("images") or [])
        if isinstance(u, str) and "wmkomiku" not in u.lower()
    ]
    return {
        "status": 200,
        "message": "ok",
        "data": {
            "images": images
            or [u for u in (data.get("images") or []) if isinstance(u, str)],
            "index": num_s,
            "title": data.get("chapter_title"),
        },
    }


def _name_list(val) -> list[str]:
    """genres/authors/artists: list[dict] atau list[str]."""
    out = []
    if not val:
        return out
    if isinstance(val, str):
        return [val]
    for x in val:
        if isinstance(x, dict) and x.get("name"):
            out.append(str(x["name"]))
        elif isinstance(x, str):
            out.append(x)
    return out


def _fmt_str(val) -> str | None:
    """format/type di latest bisa string ('Manhwa') atau list[{name}]."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.lower()
    if isinstance(val, list):
        for f in val:
            if isinstance(f, dict) and f.get("name"):
                return str(f["name"]).lower()
            if isinstance(f, str):
                return f.lower()
    return None


def _shi_item(m: dict[str, Any]) -> dict[str, Any]:
    """Map 1 item struktur official Sanka Shinigami latest → bentuk series Lumen."""
    mid = str(m.get("manga_id") or m.get("id") or "")
    title = m.get("title") or ""
    # prefer portrait; fallback cover banner
    cover = m.get("cover_portrait") or m.get("cover") or ""
    status = (m.get("status") or "").lower() or None

    # latest list: number + latest_chapter_id
    # detail: { chapter_id, chapter_number, updated_at }
    latest_raw = m.get("latest_chapter")
    latest_id = m.get("latest_chapter_id")
    latest_time = m.get("latest_chapter_time")
    latest = None
    if isinstance(latest_raw, dict):
        latest = latest_raw.get("chapter_number")
        latest_id = latest_raw.get("chapter_id") or latest_id
        latest_time = latest_raw.get("updated_at") or latest_time
    elif latest_raw is not None:
        latest = latest_raw

    fmt = _fmt_str(m.get("format"))
    if not fmt and (m.get("country") or "").upper() == "KR":
        fmt = "manhwa"
    typ = _fmt_str(m.get("type")) or "mirror"
    genres = _name_list(m.get("genres"))
    authors = _name_list(m.get("authors"))
    ch_label = f"Chapter {latest}" if latest is not None else None
    chapters = []
    if latest is not None:
        chapters.append(
            {
                "id": latest_id,
                "createdAt": latest_time,
                "data": {
                    "index": latest,
                    "title": ch_label,
                    "slug": None,
                    "chapterId": latest_id,
                },
                "provider": "shinigami",
            }
        )
    return {
        "id": mid,
        "data": {
            "title": title,
            "nativeTitle": m.get("alternative_title"),
            "slug": mid,
            "coverImage": cover,
            "author": ", ".join(authors) if authors else None,
            "rating": m.get("rating"),
            "status": status,
            "format": fmt,
            "type": typ,
            "genres": genres,
            "isHot": bool(m.get("is_recommended")),
            "totalChapters": latest,
            "provider": "shinigami",
            "latestChapterLabel": ch_label,
            "updatedLabel": latest_time or m.get("updated_at"),
            "mangaId": mid,
            "country": m.get("country"),
            "views": m.get("views"),
            "rank": m.get("rank"),
            "description": m.get("description"),
        },
        "chapters": chapters,
        "provider": "shinigami",
        "_source": "sanka_shinigami",
        "updatedAt": latest_time or m.get("updated_at"),
    }


def get_terbaru_shinigami(limit: int = 20, page: int = 1) -> dict[str, Any]:
    """
    GET /comic/shinigami/latest  (± page)
    Struktur resmi: status, creator, source, pagination, data[]
    """
    page = max(1, int(page or 1))
    path = f"/comic/shinigami/latest?page={page}"
    try:
        data = _get_json(path)
    except Exception:
        # beberapa deploy tidak terima query page
        data = _get_json("/comic/shinigami/latest")
    rows = data.get("data") or []
    items = [_shi_item(m) for m in rows[:limit] if isinstance(m, dict)]
    pag = data.get("pagination") or {}
    return {
        "status": 200,
        "message": "Sanka Shinigami latest (KC down)",
        "data": items,
        "meta": {
            "source": "sanka_shinigami",
            "creator": data.get("creator") or "Sanka Vollerei",
            "upstream_source": data.get("source") or "Shinigami",
            "total": len(items),
            "page": pag.get("current_page") or page,
            "total_pages": pag.get("total_pages"),
            "total_record": pag.get("total_record"),
            "page_size": pag.get("page_size"),
        },
    }


def get_populer_shinigami(limit: int = 20) -> dict[str, Any]:
    data = _get_json("/comic/shinigami/popular")
    rows = data.get("data") or []
    items = [_shi_item(m) for m in rows[:limit] if isinstance(m, dict)]
    return {
        "status": 200,
        "message": "Sanka Shinigami popular",
        "data": items,
        "meta": {"source": "sanka_shinigami", "total": len(items)},
    }


def get_detail_shinigami(manga_id: str) -> dict[str, Any]:
    """
    GET /comic/shinigami/detail/{manga_id}
    latest_chapter = { chapter_id, chapter_number, updated_at }
    format/type = [{ id, name, slug }]
    """
    data = _get_json(f"/comic/shinigami/detail/{manga_id}")
    m = data.get("data") or {}
    item = _shi_item(m)
    # frontend kadang baca synopsis di data.data / data.synopsis
    if isinstance(item.get("data"), dict):
        item["data"]["synopsis"] = m.get("description")
        item["data"]["description"] = m.get("description")
    return {
        "status": 200,
        "message": "ok",
        "data": item,
        "meta": {"source": "sanka_shinigami", "manga_id": manga_id},
    }


def get_chapters_shinigami(manga_id: str, max_pages: int = 8) -> dict[str, Any]:
    all_ch = []
    page = 1
    total_pages = 1
    while page <= total_pages and page <= max_pages:
        data = _get_json(f"/comic/shinigami/chapters/{manga_id}?page={page}")
        pag = data.get("pagination") or {}
        total_pages = int(pag.get("total_pages") or 1)
        for ch in data.get("data") or []:
            if not isinstance(ch, dict):
                continue
            num = ch.get("chapter_number")
            all_ch.append(
                {
                    "id": ch.get("chapter_id"),
                    "createdAt": ch.get("release_date"),
                    "data": {
                        "index": num,
                        "title": ch.get("chapter_title")
                        or (f"Chapter {num}" if num is not None else "Chapter"),
                        "slug": None,
                        "chapterId": ch.get("chapter_id"),
                    },
                    "provider": "shinigami",
                }
            )
        page += 1

    def sk(c):
        n = (c.get("data") or {}).get("index")
        try:
            return float(n)
        except (TypeError, ValueError):
            return -1

    all_ch.sort(key=sk, reverse=True)
    return {
        "status": 200,
        "message": "ok",
        "data": all_ch,
        "meta": {"source": "sanka_shinigami", "total": len(all_ch), "manga_id": manga_id},
    }


def get_pages_shinigami_by_chapter_id(chapter_id: str) -> dict[str, Any]:
    data = _get_json(f"/comic/shinigami/read/{chapter_id}")
    d = data.get("data") or {}
    images = [u for u in (d.get("images") or []) if isinstance(u, str)]
    return {
        "status": 200,
        "message": "ok",
        "data": {
            "images": images,
            "index": d.get("chapter_number"),
            "title": d.get("chapter_title")
            or (
                f"Chapter {d.get('chapter_number')}"
                if d.get("chapter_number") is not None
                else None
            ),
            "chapterId": d.get("chapter_id"),
        },
        "meta": {"source": "sanka_shinigami"},
    }


def get_pages_shinigami(manga_id: str, number) -> dict[str, Any]:
    try:
        want = float(number)
    except (TypeError, ValueError):
        want = None
    chs = get_chapters_shinigami(manga_id, max_pages=8)
    chapter_id = None
    for c in chs.get("data") or []:
        idx = (c.get("data") or {}).get("index")
        try:
            if want is not None and float(idx) == want:
                chapter_id = c.get("id") or (c.get("data") or {}).get("chapterId")
                break
        except (TypeError, ValueError):
            continue
    if not chapter_id:
        raise RuntimeError(f"chapter {number} not found for {manga_id}")
    return get_pages_shinigami_by_chapter_id(str(chapter_id))


def get_terbaru(limit: int = 20, prefer: str = "shinigami", page: int = 1) -> dict[str, Any]:
    errors = []
    if prefer == "shinigami":
        try:
            return get_terbaru_shinigami(limit=limit, page=page)
        except Exception as e:
            errors.append(f"shinigami: {e}")
        out = get_terbaru_komiku(limit=limit)
        out.setdefault("meta", {})["errors"] = errors
        return out
    try:
        return get_terbaru_komiku(limit=limit)
    except Exception as e:
        errors.append(f"komiku: {e}")
    out = get_terbaru_shinigami(limit=limit, page=page)
    out.setdefault("meta", {})["errors"] = errors
    return out


def get_populer(limit: int = 20) -> dict[str, Any]:
    try:
        return get_populer_shinigami(limit=limit)
    except Exception:
        data = _get_json("/comic/populer")
        comics = data.get("comics") or []
        items = [_komiku_item(c) for c in comics[:limit] if isinstance(c, dict)]
        return {
            "status": 200,
            "message": "Sanka populer",
            "data": items,
            "meta": {"source": "sanka_komiku", "total": len(items)},
        }


def search(q: str, limit: int = 20) -> dict[str, Any]:
    return search_komiku(q, limit=limit)
