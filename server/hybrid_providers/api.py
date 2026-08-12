#!/usr/bin/env python3
"""
REST API hybrid providers (Komikcast + Komiku) — stdlib only.

Jalankan:
  cd lumen
  python3 -m server.hybrid_providers.api

Env:
  HOST=0.0.0.0 PORT=8080

Contoh:
  GET /api/health
  GET /api/latest?limit=10&provider=komikcast
  GET /api/search?q=solo+leveling
  GET /api/manga?slug=dandadan
  GET /api/manga/chapters?komikcast=dandadan&komiku=dandadan
  GET /api/pages?provider=komikcast&slug=dandadan&number=243
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.hybrid_providers import (  # noqa: E402
    KomikcastProvider,
    KomikuProvider,
    ProviderError,
    ProviderManager,
)
from server.hybrid_providers.models import ChapterInfo  # noqa: E402
from server.hybrid_providers import mongo as mongo_cache  # noqa: E402

_mgr: ProviderManager | None = None


def get_manager() -> ProviderManager:
    global _mgr
    if _mgr is None:
        _mgr = ProviderManager([KomikcastProvider(), KomikuProvider()])
    return _mgr


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(data)


def _ok(data: Any, **extra: Any) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"ok": True, "data": data}
    body.update(extra)
    return 200, body


def _err(message: str, status: int = 400, **extra: Any) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {"ok": False, "error": message}
    body.update(extra)
    return status, body


def _qs(query: str) -> dict[str, str]:
    raw = parse_qs(query, keep_blank_values=False)
    return {k: v[0] for k, v in raw.items() if v}


def _slug_map(q: dict[str, str]) -> dict[str, str]:
    sm: dict[str, str] = {}
    if q.get("komikcast"):
        sm["komikcast"] = q["komikcast"]
    if q.get("komiku"):
        sm["komiku"] = q["komiku"]
    if q.get("slug"):
        sm.setdefault("komikcast", q["slug"])
        sm.setdefault("komiku", q["slug"])
    return sm


def handle(method: str, path: str, q: dict[str, str]) -> tuple[int, dict[str, Any]]:
    if method == "OPTIONS":
        return 200, {"ok": True}

    if path == "/" or path == "/api":
        return _ok(
            {
                "name": "Lumen Hybrid Provider API",
                "providers": ["komikcast", "komiku"],
                "endpoints": [
                    "GET /api/health",
                    "GET /api/latest?limit=20&provider=komikcast",
                    "GET /api/search?q=solo+leveling",
                    "GET /api/manga?slug=dandadan",
                    "GET /api/manga/chapters?komikcast=dandadan&komiku=dandadan",
                    "GET /api/pages?provider=komikcast&slug=dandadan&number=243",
                    "GET /api/feed?limit=10&provider=komikcast",
                ],
            }
        )

    if path == "/api/health":
        mgr = get_manager()
        return _ok(
            {
                "providers": [p.health() for p in mgr.providers],
                "mongo": mongo_cache.status(),
            }
        )

    if path == "/api/latest":
        limit = min(int(q.get("limit", "20")), 50)
        page = max(int(q.get("page", "1")), 1)
        provider = q.get("provider")
        mgr = get_manager()
        cached_from = None
        if provider:
            p = mgr.by_name(provider)
            if not p:
                return _err(f"provider tidak dikenal: {provider}", 404)
            cached = mongo_cache.cache_get_latest(provider, page)
            if cached is not None:
                return _ok(cached[:limit], count=min(len(cached), limit), cache=True)
            items = p.get_latest(page=page, limit=limit)
            payload = [m.to_dict() for m in items]
            mongo_cache.cache_set_latest(provider, page, payload)
            return _ok(payload[:limit], count=len(payload), cache=False)
        items = mgr.get_latest(limit=limit)
        return _ok([m.to_dict() for m in items], count=len(items), cache=False)

    if path == "/api/search":
        query = (q.get("q") or q.get("query") or "").strip()
        if not query:
            return _err("param q wajib")
        limit = min(int(q.get("limit", "20")), 50)
        provider = q.get("provider")
        mgr = get_manager()
        if provider:
            p = mgr.by_name(provider)
            if not p:
                return _err(f"provider tidak dikenal: {provider}", 404)
            items = p.search(query, limit=limit)
        else:
            items = mgr.search(query, limit=limit)
        return _ok([m.to_dict() for m in items], count=len(items), query=query)

    if path == "/api/manga":
        sm = _slug_map(q)
        if not sm:
            return _err("butuh ?slug=... atau ?komikcast=...&komiku=...")
        mgr = get_manager()
        try:
            info = mgr.get_manga(sm, merge=True)
        except ProviderError as e:
            return _err(str(e), 502)
        if not info:
            return _err("manga tidak ditemukan", 404)
        return _ok(info.to_dict(), slug_map=sm)

    if path == "/api/manga/chapters":
        sm = _slug_map(q)
        if not sm:
            return _err("butuh ?slug=... atau ?komikcast=...&komiku=...")
        mgr = get_manager()
        try:
            rows = mgr.get_chapters_merged(sm)
        except ProviderError as e:
            return _err(str(e), 502)
        return _ok(rows, count=len(rows), slug_map=sm)

    if path == "/api/pages":
        provider = (q.get("provider") or "").strip()
        if not provider:
            return _err("param provider wajib (komikcast|komiku)")
        mgr = get_manager()
        p = mgr.by_name(provider)
        if not p:
            return _err(f"provider tidak dikenal: {provider}", 404)

        number_raw = q.get("number")
        slug = q.get("slug")
        url = q.get("url")
        ch_number = None
        if number_raw is not None:
            try:
                ch_number = float(number_raw)
            except ValueError:
                return _err("number tidak valid")

        if not url and slug and ch_number is not None:
            try:
                chapters = p.get_chapters(slug)
            except ProviderError as e:
                return _err(str(e), 502)
            match = next(
                (
                    c
                    for c in chapters
                    if c.number is not None and float(c.number) == float(ch_number)
                ),
                None,
            )
            if not match:
                return _err(f"chapter {number_raw} tidak ada di {provider}/{slug}", 404)
            ch = match
        else:
            ch = ChapterInfo(
                number=ch_number,
                name=f"Chapter {number_raw}" if number_raw else "Chapter",
                url=url,
                provider=provider,
            )

        # cache by provider+slug+number
        slug_key = (slug or "").strip()
        if slug_key and ch_number is not None:
            cached = mongo_cache.cache_get_pages(provider, slug_key, ch_number)
            if cached is not None:
                return _ok(cached, cache=True)

        try:
            pages = p.get_pages(ch)
        except ProviderError as e:
            return _err(str(e), 502)
        payload = pages.to_dict()
        if slug_key and ch_number is not None:
            mongo_cache.cache_set_pages(provider, slug_key, ch_number, payload)
        return _ok(payload, cache=False)


    if path == "/api/feed":
        """Format mirip portal Sanka: status + data.latest[]"""
        limit = min(int(q.get("limit", "20")), 50)
        provider = q.get("provider") or "komikcast"
        mgr = get_manager()
        p = mgr.by_name(provider)
        if not p:
            # fallback merge
            items = mgr.get_latest(limit=limit)
            source_name = "hybrid"
        else:
            cached = mongo_cache.cache_get_latest(provider, 1)
            if cached is not None:
                items_data = cached[:limit]
                latest = []
                for m in items_data:
                    latest.append({
                        "manga_id": m.get("source_id") or m.get("slug"),
                        "title": m.get("title"),
                        "alternative_title": m.get("title_alt"),
                        "description": m.get("synopsis"),
                        "cover": m.get("cover_url"),
                        "status": m.get("status"),
                        "rating": m.get("rating"),
                        "genres": [{"name": g, "slug": str(g).lower().replace(" ", "-")} for g in (m.get("genres") or [])],
                        "format": m.get("type"),
                        "type": "Mirror",
                        "slug": m.get("slug"),
                        "url": m.get("source_url"),
                        "provider": m.get("provider") or provider,
                    })
                return 200, {
                    "status": "success",
                    "creator": "Sanka Comic",
                    "source": provider,
                    "cache": True,
                    "data": {"latest": latest},
                }
            items = p.get_latest(page=1, limit=limit)
            source_name = provider
            mongo_cache.cache_set_latest(provider, 1, [m.to_dict() for m in items])
        latest = []
        for m in items:
            latest.append({
                "manga_id": m.source_id or m.slug,
                "title": m.title,
                "alternative_title": m.title_alt,
                "description": m.synopsis,
                "cover": m.cover_url,
                "status": m.status,
                "rating": m.rating,
                "genres": [{"name": g, "slug": g.lower().replace(" ", "-")} for g in (m.genres or [])],
                "authors": [{"name": m.author, "slug": (m.author or "").lower().replace(" ", "-")}] if m.author else [],
                "format": m.type,
                "type": "Mirror",
                "slug": m.slug,
                "url": m.source_url,
                "provider": m.provider or source_name,
            })
        return 200, {
            "status": "success",
            "creator": "Sanka Comic",
            "source": source_name,
            "data": {"latest": latest},
        }

    if path == "/api/mongo":
        return _ok(mongo_cache.status())

    return _err("not found", 404)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = _qs(parsed.query)
        try:
            status, body = handle(self.command, path, q)
        except Exception as e:
            traceback.print_exc()
            status, body = _err(f"internal error: {e}", 500)
        _json_response(self, status, body)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Hybrid Provider API http://{host}:{port}", flush=True)
    print(
        "Endpoints: /api/health /api/latest /api/search /api/manga /api/manga/chapters /api/pages",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutdown", flush=True)
        server.server_close()


if __name__ == "__main__":
    main()
