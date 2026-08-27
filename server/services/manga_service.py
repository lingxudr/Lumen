"""Manga service — Voratoon primary, Shinigami fallback, SQLite last resort."""
from __future__ import annotations

import json
import re
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


def _is_uuid(s: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            (s or "").strip(),
        )
    )


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


def _sg():
    try:
        from server.providers import shinigami as sg

        return sg
    except Exception:
        try:
            from providers import shinigami as sg  # type: ignore

            return sg
        except Exception as e:
            print("shinigami import failed:", e, flush=True)
            return None


def _payload_ok(payload: Any) -> bool:
    if not payload or not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if data is None:
        return False
    if isinstance(data, list):
        return True  # empty list still "ok" for search; caller may retry
    if isinstance(data, dict):
        return bool(data)
    return True


def _list_nonempty(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    return isinstance(data, list) and len(data) > 0


def _sg_resolve_manga_id(slug_or_title: str) -> str | None:
    """Map Voratoon-style slug / title to Shinigami manga_id UUID."""
    sg = _sg()
    if sg is None:
        return None
    key = (slug_or_title or "").strip()
    if not key:
        return None
    if _is_uuid(key):
        return key
    q = key.replace("-", " ").strip()
    try:
        res = sg.get_series_list(take=10, page=1, q=q, mode="search")
        items = res.get("data") or []
        slug_l = key.lower()
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = it.get("manga_id") or it.get("id")
            d = it.get("data") if isinstance(it.get("data"), dict) else {}
            s = (d.get("slug") or "").lower()
            t = (d.get("title") or "").lower()
            if mid and (s == slug_l or t.replace(" ", "-") == slug_l or slug_l in t.replace(" ", "-")):
                return str(mid)
        if items and isinstance(items[0], dict):
            mid = items[0].get("manga_id") or items[0].get("id")
            if mid:
                return str(mid)
    except Exception as e:
        print("shinigami resolve id:", e, flush=True)
    return None



def _extract_images(payload: dict | None) -> list:
    """Pull image URL list from Voratoon/Lumen shaped chapter payloads."""
    if not isinstance(payload, dict):
        return []
    candidates = []
    d = payload.get("data")
    if isinstance(d, list):
        return [u for u in d if isinstance(u, str) and u.startswith("http")]
    if isinstance(d, dict):
        candidates.append(d.get("images") or d.get("pages"))
        inner = d.get("data")
        if isinstance(inner, dict):
            candidates.append(inner.get("images") or inner.get("pages"))
    candidates.append(payload.get("images"))
    for c in candidates:
        if isinstance(c, list) and c:
            out = [u for u in c if isinstance(u, str) and u.startswith("http")]
            if out:
                return out
        if isinstance(c, dict) and c:
            def sk(k):
                try:
                    return int(k)
                except Exception:
                    return str(k)
            out = [c[k] for k in sorted(c.keys(), key=sk) if isinstance(c.get(k), str) and str(c[k]).startswith("http")]
            if out:
                return out
    return []


def _ensure_page_images(payload: dict, imgs: list) -> dict:
    """Normalize so reader always finds images under data.images and data.data.images."""
    if not isinstance(payload, dict):
        return payload
    if not imgs:
        imgs = _extract_images(payload)
    d = payload.get("data")
    if not isinstance(d, dict):
        payload["data"] = {"images": imgs, "data": {"images": imgs}}
        return payload
    d["images"] = imgs or d.get("images") or []
    inner = d.get("data")
    if isinstance(inner, dict):
        inner["images"] = imgs or inner.get("images") or []
    else:
        d["data"] = {"images": imgs, "index": d.get("index") or d.get("chapterIndex"), "title": d.get("title")}
    payload["data"] = d
    meta = payload.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["total_images"] = len(imgs)
        meta["provider"] = meta.get("provider") or "voratoon"
        meta.pop("fallback", None)
    return payload


def provider_fallback(sub: str, qs: dict | None = None) -> bytes | None:
    """
    Resolve API sub-path:
      1) Voratoon (primary)
      2) Shinigami only for list/detail if Voratoon empty (NOT for pages — mismatch risk)
      3) sqlite_fallback last
    """
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

    vt = _vt()
    sg = _sg()

    # --- popular ---
    if sub0 == "popular" or (len(parts) == 1 and parts[0] == "popular"):
        if vt is not None:
            try:
                payload = vt.get_popular(take=_take(), page=_page())
                if _list_nonempty(payload):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon popular error:", e, flush=True)
        if sg is not None:
            try:
                payload = sg.get_series_list(take=_take(), page=_page(), mode="hot")
                if _payload_ok(payload):
                    payload.setdefault("meta", {})["fallback"] = "shinigami"
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("shinigami popular error:", e, flush=True)
        return None

    # --- genres ---
    if sub0 == "genres" or (len(parts) == 1 and parts[0] == "genres"):
        if vt is not None:
            try:
                payload = vt.get_genres()
                if _list_nonempty(payload):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon genres error:", e, flush=True)
        if sg is not None:
            try:
                payload = sg.get_genres()
                if _payload_ok(payload):
                    payload.setdefault("meta", {})["fallback"] = "shinigami"
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("shinigami genres error:", e, flush=True)
        return json.dumps({"status": 200, "data": [], "meta": {}}).encode()

    # --- LIST series ---
    if sub0 == "series" or (len(parts) == 1 and parts[0] == "series"):
        take = _take()
        page = _page()
        sort = (qs.get("sort") or [""])[0]
        status = (qs.get("status") or [""])[0]
        fmt = (qs.get("format") or [""])[0]
        type_q = (qs.get("type") or [""])[0]
        preset = (qs.get("preset") or [""])[0]
        mode_q = (qs.get("mode") or [""])[0].strip()
        is_hot = (qs.get("isHot") or [""])[0]
        qsearch = (qs.get("title") or qs.get("q") or qs.get("search") or [""])[0].strip()
        genre = (qs.get("genre") or [""])[0]
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

        if vt is not None:
            try:
                payload = vt.get_series_list(
                    take=take,
                    page=page,
                    sort=sort or "updatedAt",
                    q=qsearch,
                    status=status,
                    format_=fmt,
                    take_chapter=take_ch,
                    mode=mode,
                    type_=type_q,
                    genre=genre,
                )
                # search must be nonempty to count as success; feeds can be empty rarely
                if qsearch:
                    if _list_nonempty(payload):
                        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
                elif _payload_ok(payload):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon list error:", e, flush=True)

        if sg is not None:
            try:
                sg_mode = "search" if qsearch else ("hot" if mode == "hot" else "newest")
                payload = sg.get_series_list(
                    take=take, page=page, q=qsearch, mode=sg_mode
                )
                if _payload_ok(payload):
                    payload.setdefault("meta", {})["fallback"] = "shinigami"
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("shinigami list error:", e, flush=True)
        return None

    kind, slug, chapter = _parse_series_sub(sub)
    if not slug:
        return None

    # UUID path → prefer Shinigami directly
    if _is_uuid(slug) and sg is not None:
        try:
            if kind == "detail":
                payload = sg.get_series_detail(slug)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if kind == "chapters":
                payload = sg.get_chapters(slug, page=_page(), page_size=_take() or 48)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if kind == "pages" and chapter:
                # chapter may be UUID or number
                if _is_uuid(str(chapter)):
                    payload = sg.get_pages(str(chapter))
                else:
                    payload = _sg_pages_by_number(slug, str(chapter))
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print("shinigami uuid path error:", e, flush=True)

    # --- detail ---
    if kind == "detail":
        if vt is not None:
            try:
                payload = vt.get_series_detail(slug)
                if payload and (payload.get("data") or payload.get("status") == 200):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon detail error:", e, flush=True)
        if sg is not None:
            try:
                mid = _sg_resolve_manga_id(slug)
                if mid:
                    payload = sg.get_series_detail(mid)
                    if payload:
                        payload.setdefault("meta", {})["fallback"] = "shinigami"
                        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("shinigami detail error:", e, flush=True)
        return sqlite_fallback(sub)

    # --- chapters ---
    if kind == "chapters":
        if vt is not None:
            try:
                payload = vt.get_chapters(slug)
                if _list_nonempty(payload):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon chapters error:", e, flush=True)
        if sg is not None:
            try:
                mid = _sg_resolve_manga_id(slug)
                if mid:
                    # first page is enough for UI; full list optional
                    payload = sg.get_chapters(mid, page=_page(), page_size=48)
                    if _list_nonempty(payload):
                        payload.setdefault("meta", {})["fallback"] = "shinigami"
                        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("shinigami chapters error:", e, flush=True)
        return sqlite_fallback(sub)

    # --- pages ---
    if kind == "pages":
        if vt is not None:
            try:
                payload = vt.get_pages(slug, chapter)
                imgs = _extract_images(payload)
                # Also accept meta.total_images > 0
                meta = (payload or {}).get("meta") if isinstance(payload, dict) else {}
                n_meta = 0
                try:
                    n_meta = int((meta or {}).get("total_images") or 0)
                except Exception:
                    n_meta = 0
                if imgs or n_meta > 0:
                    # Ensure client always sees images at data.data.images AND data.images
                    payload = _ensure_page_images(payload, imgs)
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
                print(
                    f"voratoon pages empty slug={slug} ch={chapter}",
                    flush=True,
                )
            except Exception as e:
                print("voratoon pages error:", e, flush=True)
        # Shinigami pages fallback DISABLED — title/ch number match often maps wrong manga
        # (same poster from Voratoon, images from another series on Shinigami).
        return sqlite_fallback(sub)

    return None


def _sg_pages_by_number(manga_id: str, chapter_ref: str) -> dict[str, Any] | None:
    """Find chapter by number (or UUID) then return pages payload."""
    sg = _sg()
    if sg is None:
        return None
    if _is_uuid(chapter_ref):
        return sg.get_pages(chapter_ref)

    try:
        want = float(chapter_ref)
    except Exception:
        want = None

    # scan a few pages of chapter list for matching number
    for page in range(1, 6):
        try:
            chs = sg.get_chapters(manga_id, page=page, page_size=48)
        except Exception:
            break
        for row in chs.get("data") or []:
            if not isinstance(row, dict):
                continue
            idx = row.get("chapterIndex")
            if idx is None and isinstance(row.get("data"), dict):
                idx = row["data"].get("index")
            cid = row.get("chapter_id") or row.get("id")
            try:
                if want is not None and idx is not None and float(idx) == want and cid:
                    return sg.get_pages(str(cid))
            except Exception:
                continue
        meta = chs.get("meta") or {}
        if page >= int(meta.get("lastPage") or 1):
            break
    return None


# Back-compat alias used by app.py / cache_warmer
sanka_fallback = provider_fallback


def resolve_upstream_failure(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    body = provider_fallback(sub, qs)
    if body:
        try:
            meta = json.loads(body).get("meta") or {}
            src = meta.get("fallback") or meta.get("provider") or meta.get("source") or "voratoon"
            if "shinigami" in str(src).lower():
                return body, "shinigami"
            return body, "voratoon"
        except Exception:
            return body, "voratoon"
    body = sqlite_fallback(sub)
    if body:
        return body, "sqlite"
    return None, "none"


def provider_status(deep: bool = False) -> dict[str, Any]:
    vt = _vt()
    sg = _sg()
    providers = [
        {
            "provider": "voratoon",
            "priority": 1,
            "status": "configured" if vt else "missing",
            "capabilities": [
                "search",
                "latest",
                "detail",
                "chapters",
                "pages",
                "genres",
                "popular",
            ],
        },
        {
            "provider": "shinigami",
            "priority": 2,
            "status": "configured" if sg else "missing",
            "capabilities": ["search", "latest", "detail", "chapters", "pages", "genres", "popular"],
            "role": "fallback",
        },
    ]
    status: dict[str, Any] = {"ok": True, "providers": providers}
    if deep:
        if vt is not None:
            try:
                p = vt.get_series_list(take=1, page=1, mode="newest")
                providers[0]["status"] = (
                    "healthy" if p and p.get("data") is not None else "degraded"
                )
            except Exception as e:
                providers[0]["status"] = "error"
                providers[0]["error"] = str(e)[:200]
        if sg is not None:
            try:
                h = sg.health()
                providers[1]["status"] = h.get("status") or "unknown"
                providers[1]["latency_ms"] = h.get("latency_ms")
            except Exception as e:
                providers[1]["status"] = "error"
                providers[1]["error"] = str(e)[:200]
    return status


def catalog_newest(take: int = 20) -> bytes | None:
    return provider_fallback(
        "series", {"take": [str(take)], "page": ["1"], "mode": ["newest"]}
    )


def resolve_list_request(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    return resolve_upstream_failure(sub, qs)
