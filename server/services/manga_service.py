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


def sanka_fallback(sub: str, qs: dict | None = None) -> bytes | None:
    """Sanka (Shinigami → Komiku-style) saat KC + SQLite kosong."""
    if sanka_provider is None:
        return None
    qs = qs or {}
    try:
        sub0 = (sub or "").split("?")[0].strip("/")
        parts = [x for x in sub0.split("/") if x]

        def _take() -> int:
            try:
                return int((qs.get("take") or qs.get("limit") or ["20"])[0])
            except Exception:
                return 20

        if sub0 == "series" or (len(parts) == 1 and parts[0] == "series"):
            sort = (qs.get("sort") or ["updatedAt"])[0]
            qsearch = (qs.get("q") or qs.get("search") or [""])[0].strip()
            take = _take()
            if qsearch:
                payload = sanka_provider.search(qsearch, limit=take)
            elif sort in ("popular", "popularity", "hot", "views"):
                payload = sanka_provider.get_populer(limit=take)
            else:
                try:
                    page = int((qs.get("page") or ["1"])[0])
                except Exception:
                    page = 1
                payload = sanka_provider.get_terbaru(
                    limit=take, prefer="shinigami", page=page
                )
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        kind, slug, chapter = _parse_series_sub(sub)
        if not slug:
            return None

        if getattr(sanka_provider, "looks_like_uuid", lambda _s: False)(slug):
            if kind == "detail" or (len(parts) == 2 and parts[0] == "series"):
                payload = sanka_provider.get_detail_shinigami(slug)
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if kind == "chapters" or (len(parts) >= 3 and parts[-1] == "chapters"):
                payload = sanka_provider.get_chapters_shinigami(slug)
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if kind == "pages" and chapter is not None:
                payload = sanka_provider.get_pages_shinigami(slug, chapter)
                return json.dumps(payload, ensure_ascii=False).encode("utf-8")

        if kind == "pages" and chapter is not None:
            payload = sanka_provider.get_chapter_images_komiku(slug, chapter)
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        print("manga_service.sanka_fallback error:", e, flush=True)
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
    """Health ringkas untuk /api/health."""
    out: dict[str, Any] = {
        "sqlite": lumen_db is not None,
        "sanka": sanka_provider is not None,
        "source_of_truth": "provider_live",
        "cache": "sqlite_read_through",
        "mongo": "optional_catalog_only",
    }
    if sanka_provider is not None:
        try:
            sample = sanka_provider.get_terbaru(limit=1, prefer="shinigami", page=1)
            out["sanka_ok"] = bool(sample.get("data"))
            out["sanka_meta"] = sample.get("meta")
        except Exception as e:
            out["sanka_ok"] = False
            out["sanka_error"] = str(e)
    return out
