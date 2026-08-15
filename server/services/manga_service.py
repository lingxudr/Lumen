"""
MangaService — satu pintu untuk list / detail / chapters / pages.

Alur (source of truth = provider live):
  1. Komikcast upstream (jika hidup)
  2. SQLite read-through cache (stale-while-revalidate)
  3. Sanka Shinigami (fallback saat KC down)
  4. Sanka Komiku-style (cadangan terakhir untuk search/pages)

MongoDB (opsional):
  - Catalog / sync metadata saja
  - BUKAN source of truth konten chapter/pages
"""

from __future__ import annotations

import json
import traceback
from typing import Any
from urllib.parse import parse_qs

# Optional DB
try:
    import db as lumen_db
except Exception:
    try:
        from server import db as lumen_db  # type: ignore
    except Exception:
        lumen_db = None  # type: ignore

try:
    from server.providers import sanka as sanka_provider
except Exception:
    try:
        from providers import sanka as sanka_provider  # type: ignore
    except Exception:
        try:
            import sanka_fallback as sanka_provider  # type: ignore
        except Exception:
            sanka_provider = None  # type: ignore


def _parse_series_sub(sub: str) -> tuple[str | None, str | None, str | None]:
    s = (sub or "").split("?")[0].strip("/")
    parts = [p for p in s.split("/") if p]
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
    return None, slug, None


def sqlite_fallback(sub: str) -> bytes | None:
    """Baca SQLite cache bila upstream gagal."""
    if lumen_db is None:
        return None
    kind, slug, chapter = _parse_series_sub(sub)
    try:
        sub0 = (sub or "").split("?")[0].strip("/")
        if sub0 == "series" and not slug:
            take = 20
            if "?" in (sub or ""):
                q = parse_qs((sub or "").split("?", 1)[-1])
                try:
                    take = int((q.get("take") or q.get("limit") or ["20"])[0])
                except Exception:
                    take = 20
            if hasattr(lumen_db, "get_newest_list"):
                return lumen_db.get_newest_list(limit=take)
            return None
        if kind == "detail" and slug:
            raw = lumen_db.get_manga(slug)
            if raw and hasattr(lumen_db, "wrap_manga_detail"):
                return lumen_db.wrap_manga_detail(raw)
            return raw
        if kind == "chapters" and slug:
            return lumen_db.get_chapter_list(slug)
        if kind == "pages" and slug and chapter is not None:
            return lumen_db.get_chapter_pages(slug, chapter)
    except Exception:
        traceback.print_exc()
    return None


# Set True when Sanka returns permanent ban (403) — skip further Sanka hits
_SANKA_BANNED = False
_SANKA_BAN_REASON = ""


def _mark_sanka_banned(err: Exception | str) -> None:
    global _SANKA_BANNED, _SANKA_BAN_REASON
    msg = str(err)
    if "403" in msg or "banned" in msg.lower() or "permanently banned" in msg.lower():
        _SANKA_BANNED = True
        _SANKA_BAN_REASON = msg[:200]
        print("manga_service: Sanka marked BANNED —", _SANKA_BAN_REASON, flush=True)


def _manga_info_to_series_item(m) -> dict:
    """MangaInfo / dict → shape frontend list card."""
    if hasattr(m, "to_dict"):
        d = m.to_dict()
    elif isinstance(m, dict):
        d = m
    else:
        return {}
    slug = d.get("slug") or d.get("source_slug") or ""
    cover = d.get("cover_url") or d.get("coverImage") or d.get("cover") or ""
    return {
        "id": slug,
        "data": {
            "title": d.get("title") or slug,
            "nativeTitle": d.get("title_alt") or "",
            "slug": slug,
            "coverImage": cover,
            "author": d.get("author") or "",
            "rating": d.get("rating"),
            "status": (d.get("status") or "").lower() if d.get("status") else "",
            "format": (d.get("type") or "").lower() if d.get("type") else "",
            "type": "mirror",
            "genres": d.get("genres") or [],
            "isHot": False,
            "totalChapters": None,
            "provider": d.get("provider") or "komiku",
            "latestChapterLabel": d.get("latest_chapter"),
            "updatedLabel": d.get("updated_label"),
            "synopsis": d.get("synopsis") or "",
        },
        "chapters": [],
        "provider": d.get("provider") or "komiku",
        "_source": "komiku_direct",
        "updatedAt": None,
    }


def _komiku_list_payload(take: int = 20, page: int = 1, q: str = "", popular: bool = False) -> dict | None:
    """Direct komiku.org via KomikuProvider — bypass Sanka ban."""
    try:
        try:
            from server.hybrid_providers.providers.komiku import KomikuProvider
        except Exception:
            from hybrid_providers.providers.komiku import KomikuProvider  # type: ignore
        p = KomikuProvider()
        if q:
            items = p.search(q, limit=take)
        elif popular and hasattr(p, "get_ranking"):
            items = p.get_ranking("mingguan", limit=take)
        else:
            items = p.get_latest(limit=take, page=page)
        data = [_manga_info_to_series_item(m) for m in items]
        data = [x for x in data if x.get("data", {}).get("slug")]
        return {
            "status": 200,
            "message": "Komiku direct (Sanka banned / KC down)",
            "data": data,
            "meta": {
                "source": "komiku_direct",
                "page": page,
                "lastPage": page + (1 if len(data) >= take else 0),
                "total": len(data),
                "take": take,
                "sanka_banned": _SANKA_BANNED,
            },
        }
    except Exception as e:
        print("komiku_direct list error:", e, flush=True)

    # Last resort: bundled seed (when Railway IP blocked from all mirrors)
    try:
        from pathlib import Path
        seed_paths = [
            Path(__file__).resolve().parent.parent / "static" / "komiku_seed_latest.json",
            Path(__file__).resolve().parent.parent / "data" / "komiku_seed_latest.json",
            Path("server/static/komiku_seed_latest.json"),
            Path("server/data/komiku_seed_latest.json"),
            Path("/app/server/static/komiku_seed_latest.json"),
        ]
        for sp in seed_paths:
            if sp.is_file():
                import json as _json
                payload = _json.loads(sp.read_text(encoding="utf-8"))
                data = payload.get("data") or []
                if page > 1:
                    # seed only has page 1
                    return {
                        "status": 200,
                        "message": "Seed cache (page limited)",
                        "data": [],
                        "meta": {"source": "seed_file", "page": page, "lastPage": 1, "total": 0},
                    }
                payload["data"] = data[:take]
                payload.setdefault("meta", {})["source"] = "seed_file"
                payload["meta"]["take"] = take
                print("komiku_direct: using seed file", sp, flush=True)
                return payload
    except Exception as e2:
        print("seed load error:", e2, flush=True)
    return None


def _komiku_detail_payload(slug: str) -> dict | None:
    try:
        try:
            from server.hybrid_providers.providers.komiku import KomikuProvider
        except Exception:
            from hybrid_providers.providers.komiku import KomikuProvider  # type: ignore
        p = KomikuProvider()
        info = p.get_manga(slug)
        if not info:
            import re
            if re.fullmatch(r"[0-9a-fA-F-]{36}", slug or ""):
                return None
            # search fallback only for human slugs
            hits = p.search(slug.replace("-", " "), limit=5)
            for h in hits:
                if (h.slug or h.source_slug) == slug or slug in (h.slug or ""):
                    info = h
                    break
            if not info and hits:
                info = hits[0]
                slug = info.slug or info.source_slug or slug
        if not info:
            return None
        item = _manga_info_to_series_item(info)
        # enrich synopsis via get_manga if thin
        return {
            "status": 200,
            "message": "Komiku detail",
            "data": item,
            "meta": {"source": "komiku_direct"},
        }
    except Exception as e:
        print("komiku_direct detail error:", e, flush=True)
        return None


def _komiku_chapters_payload(slug: str) -> dict | None:
    try:
        try:
            from server.hybrid_providers.providers.komiku import KomikuProvider
        except Exception:
            from hybrid_providers.providers.komiku import KomikuProvider  # type: ignore
        from server.hybrid_providers.chapter_dedup import parse_chapter_number
        p = KomikuProvider()
        chs = p.get_chapters(slug)
        data = []
        for ch in chs:
            num = ch.number
            if num is None:
                num = parse_chapter_number(ch.name)
            data.append({
                "id": ch.source_chapter_id or ch.url or f"{slug}-{num}",
                "createdAt": ch.published_at,
                "updatedAt": ch.published_at,
                "data": {
                    "index": num,
                    "title": ch.name or (f"Chapter {num}" if num is not None else "Chapter"),
                    "slug": None,
                    "chapterId": ch.source_chapter_id or ch.url,
                },
                "provider": "komiku",
            })
        return {
            "status": 200,
            "message": "Komiku chapters",
            "data": data,
            "meta": {"source": "komiku_direct", "total": len(data)},
        }
    except Exception as e:
        print("komiku_direct chapters error:", e, flush=True)
        return None


def sanka_fallback(sub: str, qs: dict | None = None) -> bytes | None:
    """
    Nama historis (dipakai app.py).
    Rantai: Voratoon API → Komiku (jika ada) → seed file.
    Sanka dihapus (IP banned). Komiku sering 403 di Railway.
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

    def _is_uuid(s: str) -> bool:
        import re as _re
        return bool(_re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            (s or "").strip(),
        ))

    # --- Voratoon client ---
    try:
        try:
            from server.providers import voratoon as vt
        except Exception:
            from providers import voratoon as vt  # type: ignore
    except Exception as e:
        print("voratoon import failed:", e, flush=True)
        vt = None

    # LIST
    if sub0 == "series" or (len(parts) == 1 and parts[0] == "series"):
        sort = (qs.get("sort") or ["updatedAt"])[0]
        qsearch = (qs.get("q") or qs.get("search") or [""])[0].strip()
        take = _take()
        page = _page()
        status = (qs.get("status") or [""])[0]
        fmt = (qs.get("format") or [""])[0]
        if vt is not None:
            try:
                payload = vt.get_series_list(
                    take=take,
                    page=page,
                    sort=sort,
                    q=qsearch,
                    status=status,
                    format_=fmt,
                )
                if payload and payload.get("data"):
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon list error:", e, flush=True)
        # Komiku fallback
        popular = sort in ("popular", "popularity", "hot", "views")
        payload = _komiku_list_payload(take=take, page=page, q=qsearch, popular=popular)
        if payload and payload.get("data"):
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return None

    kind, slug, chapter = _parse_series_sub(sub)
    if not slug:
        return None
    if _is_uuid(slug):
        print("fallback: skip UUID slug", slug, flush=True)
        return None

    # DETAIL
    if kind == "detail" or (len(parts) == 2 and parts[0] == "series"):
        if vt is not None:
            try:
                payload = vt.get_series_detail(slug)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon detail error:", e, flush=True)
        payload = _komiku_detail_payload(slug)
        if payload:
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return None

    # CHAPTERS
    if kind == "chapters" or (len(parts) >= 3 and parts[-1] == "chapters"):
        if vt is not None:
            try:
                payload = vt.get_chapters(slug)
                if payload and payload.get("data") is not None:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon chapters error:", e, flush=True)
        payload = _komiku_chapters_payload(slug)
        if payload:
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return None

    # PAGES
    if kind == "pages" and chapter is not None:
        if vt is not None:
            try:
                payload = vt.get_pages(slug, chapter)
                if payload:
                    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception as e:
                print("voratoon pages error:", e, flush=True)
        # komiku pages last
        try:
            try:
                from server.hybrid_providers.providers.komiku import KomikuProvider
            except Exception:
                from hybrid_providers.providers.komiku import KomikuProvider  # type: ignore
            p = KomikuProvider()
            chs = p.get_chapters(slug)
            target = None
            try:
                want = float(chapter)
            except Exception:
                want = None
            for ch in chs:
                if want is not None and ch.number is not None and abs(float(ch.number) - want) < 0.001:
                    target = ch
                    break
            if target is None and chs:
                target = chs[0]
            if target:
                pages = p.get_pages(target)
                return json.dumps({
                    "status": 200,
                    "message": "ok",
                    "data": {
                        "data": {
                            "images": pages.images,
                            "index": target.number,
                            "title": target.name,
                        },
                        "chapterIndex": target.number,
                    },
                    "meta": {"provider": "komiku", "source": "komiku_direct"},
                }, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            print("komiku pages error:", e, flush=True)
    return None



def resolve_upstream_failure(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    """
    Chain fallback setelah Komikcast gagal.
    Returns (body, source_tag) source_tag: sqlite | sanka | none
    """
    body = sqlite_fallback(sub)
    if body:
        return body, "sqlite"
    body = sanka_fallback(sub, qs)
    if body:
        return body, "sanka"
    return None, "none"


def provider_status() -> dict[str, Any]:
    """Health ringkas untuk /api/health — lewat ProviderManager bila ada."""
    out: dict[str, Any] = {
        "sqlite": lumen_db is not None,
        "sanka": False,
        "sanka_removed": True,
        "voratoon": True,
        "sanka_banned": True,
        "sanka_ban_reason": "removed: IP permanently banned",
        "sanka_ok": False,
        "source_of_truth": "canonical_db_when_synced_else_provider",
        "cache": "sqlite_read_through",
        "mongo": "optional_catalog_primary_when_present",
        "architecture": "ProviderManager is single authority",
        "fallback_chain": ["komikcast", "voratoon", "komiku_direct", "seed"],
    }
    try:
        try:
            from server.hybrid_providers import default_manager
        except Exception:
            from hybrid_providers import default_manager  # type: ignore
        mgr = default_manager()
        out["providers"] = mgr.health_snapshot()
        if all(r.get("successes", 0) + r.get("failures", 0) == 0 for r in out["providers"]):
            out["probe"] = mgr.probe_all()
            out["providers"] = mgr.health_snapshot()
    except Exception as e:
        out["manager_error"] = str(e)
        if sanka_provider is not None:
            try:
                sample = sanka_provider.get_terbaru(limit=1, prefer="shinigami", page=1)
                out["sanka_ok"] = bool(sample.get("data"))
            except Exception as e2:
                out["sanka_ok"] = False
                out["sanka_error"] = str(e2)
    return out


def catalog_newest(take: int = 20) -> bytes | None:
    """
    DB-first: baca canonical catalog Mongo bila ada.
    Return body JSON bentuk series list, atau None.
    """
    try:
        from server.hybrid_providers import mongo as mongo_cache
    except Exception:
        try:
            from hybrid_providers import mongo as mongo_cache  # type: ignore
        except Exception:
            return None
    db = mongo_cache.get_db()
    if db is None:
        return None
    try:
        cur = db.catalog.find({}).sort("updated_at", -1).limit(max(1, min(take, 50)))
        items = []
        for d in cur:
            slug = d.get("canonical_slug") or (d.get("slug_map") or {}).get("komikcast")
            if not slug:
                continue
            items.append(
                {
                    "id": slug,
                    "data": {
                        "title": d.get("title"),
                        "slug": slug,
                        "coverImage": d.get("cover_url"),
                        "status": (d.get("status") or "").lower() or None,
                        "format": (d.get("type") or "").lower() or None,
                        "provider": "canonical",
                        "latestChapterLabel": d.get("latest_chapter"),
                        "updatedLabel": d.get("updated_label"),
                        "providers": d.get("providers") or [],
                    },
                    "chapters": [],
                    "provider": "canonical",
                    "_source": "mongo_catalog",
                }
            )
        if not items:
            return None
        payload = {
            "status": 200,
            "message": "Canonical catalog (DB-first)",
            "data": items,
            "meta": {
                "source": "mongo_catalog",
                "total": len(items),
                "page": 1,
                "lastPage": 1,
            },
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        print("catalog_newest error:", e, flush=True)
        return None


def resolve_list_request(sub: str, qs: dict | None = None) -> tuple[bytes | None, str]:
    """
    DB-first list:
      1. Mongo catalog (jika ada data segar)
      2. SQLite
      3. Sanka live
    """
    qs = qs or {}
    try:
        take = int((qs.get("take") or qs.get("limit") or ["20"])[0])
    except Exception:
        take = 20
    body = catalog_newest(take=take)
    if body:
        return body, "mongo_catalog"
    return resolve_upstream_failure(sub, qs)
