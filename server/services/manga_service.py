"""Manga service — Voratoon provider only."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from server import db as lumen_db
except Exception:
    try:
        import db as lumen_db  # type: ignore
    except Exception:
        lumen_db = None  # type: ignore


def _parse_series_sub(sub: str) -> tuple[str | None, str | None, str | None]:
    """Return (kind, slug, chapter) for series/* paths."""
    sub0 = (sub or "").split("?")[0].strip("/")
    parts = [x for x in sub0.split("/") if x]
    if not parts or parts[0] != "series":
        return None, None, None
    if len(parts) == 1:
        return "list", None, None
    slug = parts[1]
    if len(parts) == 2:
        return "detail", slug, None
    if len(parts) == 3 and parts[2] == "chapters":
        return "chapters", slug, None
    if len(parts) >= 4 and parts[2] == "chapters":
        return "pages", slug, parts[3]
    return "detail", slug, None


def sqlite_fallback(sub: str) -> bytes | None:
    if lumen_db is None:
        return None
    kind, slug, chapter = _parse_series_sub(sub)
    if not slug:
        return None
    try:
        if kind == "detail":
            row = lumen_db.get_manga(slug)
            if row:
                return row if isinstance(row, (bytes, bytearray)) else json.dumps(row).encode()
        if kind == "chapters":
            row = lumen_db.get_chapter_list(slug)
            if row:
                return row if isinstance(row, (bytes, bytearray)) else json.dumps(row).encode()
        if kind == "pages" and chapter is not None:
            row = lumen_db.get_chapter_pages(slug, chapter)
            if row:
                return row if isinstance(row, (bytes, bytearray)) else json.dumps(row).encode()
    except Exception as e:
        print("sqlite_fallback:", e, flush=True)
    return None


def _vt():
    try:
        from server.providers import voratoon as vt
        return vt
    except Exception:
        try:
            from providers import voratoon as vt  # type: ignore
            return vt
        except Exception as e:
            print("voratoon import failed:", e, flush=True)
            return None


def provider_fallback(sub: str, qs: dict | None = None) -> bytes | None:
    """Resolve API sub-path via Voratoon. (Historically named sanka_fallback.)"""
    qs = qs or {}
    sub0 = (sub or "").split("?")[0].strip("/")
    parts = [x for x in sub0.split("/") if x]

    def _take() -> int:
        try:
            return int((qs.get("take") or qs.get("limit") or ["20"])[0])
        except Exception:
            return 20

    def _page() -> int:
        try:
            return int((qs.get("page") or ["1"])[0])
        except Exception:
            return 1

    def _is_uuid(s: str) -> bool:
        return bool(
            re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                (s or "").strip(),
            )
        )

    vt = _vt()

    if sub0 == "popular" or (len(parts) == 1 and parts[0] == "popular"):
        if vt is None:
            return None
        try:
            payload = vt.get_popular(take=_take(), page=_page())
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print("voratoon popular error:", e, flush=True)
            return None

    if sub0 == "genres" or (len(parts) == 1 and parts[0] == "genres"):
        if vt is None:
            return None
        try:
            payload = vt.get_genres()
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print("voratoon genres error:", e, flush=True)
            return json.dumps({"status": 200, "data": [], "meta": {"error": str(e)}}).encode()

    # LIST series
    if sub0 == "series" or (len(parts) == 1 and parts[0] == "series"):
        if vt is None:
            return None
        try:
            take = _take()
            page = _page()
            sort = (qs.get("sort") or [""])[0]
            status = (qs.get("status") or [""])[0]
            fmt = (qs.get("format") or qs.get("type") or [""])[0]
            type_q = (qs.get("type") or [""])[0]
            preset = (qs.get("preset") or [""])[0]
            mode_q = (qs.get("mode") or [""])[0]
            is_hot = (qs.get("isHot") or qs.get("hot") or [""])[0]
            qsearch = (qs.get("title") or qs.get("q") or qs.get("search") or [""])[0].strip()
            take_ch = 3
            try:
                take_ch = int((qs.get("takeChapter") or ["3"])[0])
            except Exception:
                take_ch = 3
            mode = mode_q or "newest"
            if sort in ("popular", "popularity", "hot", "views") or str(is_hot).lower() in (
                "1",
                "true",
                "yes",
            ):
                mode = "hot"
            if preset in ("rilisan_terbaru", "newest", "latest"):
                mode = mode_q or "newest"
            if preset in ("new_series", "series_baru", "baru"):
                mode = "new_series"
            if preset in ("completed", "complete", "selesai") or status in (
                "completed",
                "complete",
            ):
                mode = "completed"
            if type_q == "project" and mode == "newest":
                mode = "project"
            if qsearch:
                mode = "search"
            if mode_q == "browse" or (qs.get("browse") or [""])[0] in ("1", "true"):
                mode = "browse"
            if mode in ("newest", "") and (status or fmt) and not qsearch:
                mode = "browse"
            genre = (qs.get("genre") or [""])[0]
            payload = vt.get_series_list(
                take=take,
                page=page,
                sort=sort,
                q=qsearch,
                status=status,
                format_=fmt,
                take_chapter=take_ch,
                mode=mode,
                type_=type_q,
                genre=genre,
            )
            if payload and payload.get("data") is not None:
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print("voratoon list error:", e, flush=True)
        return None

    kind, slug, chapter = _parse_series_sub(sub)
    if not slug:
        return None
    if _is_uuid(slug):
        print("fallback: skip UUID slug", slug, flush=True)
        return None

    if kind == "detail" or (len(parts) == 2 and parts[0] == "series"):
        if vt is not None:
            try:
                payload = vt.get_series_detail(slug)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon detail error:", e, flush=True)
        return sqlite_fallback(sub)

    if kind == "chapters":
        if vt is not None:
            try:
                payload = vt.get_chapters(slug)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon chapters error:", e, flush=True)
        return sqlite_fallback(sub)

    if kind == "pages":
        if vt is not None:
            try:
                payload = vt.get_pages(slug, chapter)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon pages error:", e, flush=True)
        return sqlite_fallback(sub)

    return None


# Back-compat alias used by app.py / cache_warmer
sanka_fallback = provider_fallback


def resolve_upstream_failure(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    body = provider_fallback(sub, qs)
    if body:
        return body, "voratoon"
    body = sqlite_fallback(sub)
    if body:
        return body, "sqlite"
    return None, "none"


def provider_status(deep: bool = False) -> dict[str, Any]:
    vt = _vt()
    status = {
        "ok": True,
        "providers": [
            {
                "provider": "voratoon",
                "status": "configured" if vt else "missing",
                "capabilities": ["search", "latest", "detail", "chapters", "pages", "genres", "popular"],
            }
        ],
    }
    if deep and vt is not None:
        try:
            p = vt.get_series_list(take=1, page=1, mode="newest")
            status["providers"][0]["status"] = "healthy" if p and p.get("data") is not None else "degraded"
        except Exception as e:
            status["providers"][0]["status"] = "error"
            status["providers"][0]["error"] = str(e)
    return status


def catalog_newest(take: int = 20) -> bytes | None:
    return provider_fallback("series", {"take": [str(take)], "page": ["1"], "mode": ["newest"]})


def resolve_list_request(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    body = provider_fallback(sub, qs)
    if body:
        return body, "voratoon"
    return None, "none"
