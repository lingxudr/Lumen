"""
Dynamic sitemap for Lumen (auto-index manga URLs).

Endpoints (via app.py):
  GET /sitemap.xml          → urlset (static pages + recent manga)
  GET /api/sitemap          → same
  GET /sitemap-index.xml    → sitemap index if multi-page

Env:
  SITEMAP_SITE=https://www.v1lumen.my.id
  SITEMAP_PAGES=5          # /updates pages to pull (~30 each)
  SITEMAP_TTL=3600
"""
from __future__ import annotations

import os
import time
import xml.sax.saxutils as sax
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

_CACHE: dict[str, Any] = {"xml": None, "exp": 0.0, "index": None, "index_exp": 0.0}

SITE = (os.environ.get("SITEMAP_SITE") or os.environ.get("PUBLIC_SITE") or "https://www.v1lumen.my.id").rstrip("/")
TTL = float(os.environ.get("SITEMAP_TTL", "3600"))
MAX_PAGES = int(os.environ.get("SITEMAP_PAGES", "5"))


def _esc(s: str) -> str:
    return sax.escape(s or "")


def _url(loc: str, *, changefreq: str = "daily", priority: str = "0.5", lastmod: str | None = None) -> str:
    parts = [f"  <url>", f"    <loc>{_esc(loc)}</loc>"]
    if lastmod:
        parts.append(f"    <lastmod>{_esc(lastmod)}</lastmod>")
    parts.append(f"    <changefreq>{changefreq}</changefreq>")
    parts.append(f"    <priority>{priority}</priority>")
    parts.append("  </url>")
    return "\n".join(parts)


def _static_urls(now: str) -> list[str]:
    return [
        _url(f"{SITE}/", changefreq="hourly", priority="1.0", lastmod=now),
        _url(f"{SITE}/latest", changefreq="hourly", priority="0.9", lastmod=now),
        _url(f"{SITE}/popular", changefreq="daily", priority="0.85", lastmod=now),
        _url(f"{SITE}/latest?tab=completed", changefreq="daily", priority="0.7", lastmod=now),
        _url(f"{SITE}/latest?tab=new_series", changefreq="daily", priority="0.7", lastmod=now),
        _url(f"{SITE}/latest?tab=browse", changefreq="daily", priority="0.65", lastmod=now),
        _url(f"{SITE}/search", changefreq="weekly", priority="0.5", lastmod=now),
        _url(f"{SITE}/lumenrest/docs", changefreq="monthly", priority="0.3", lastmod=now),
    ]


def _collect_slugs(max_pages: int) -> list[tuple[str, str | None]]:
    """Return list of (slug, lastmod_iso)."""
    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    try:
        from providers import voratoon as vt
    except Exception:
        try:
            from server.providers import voratoon as vt  # type: ignore
        except Exception as e:
            print("sitemap voratoon import:", e, flush=True)
            return out

    for page in range(1, max(1, max_pages) + 1):
        try:
            payload = vt.fetch_updates_html(page=page)
        except Exception as e:
            print(f"sitemap updates page {page}:", e, flush=True)
            break
        items = payload.get("data") or []
        if not items:
            break
        for it in items:
            if not isinstance(it, dict):
                continue
            d = it.get("data") if isinstance(it.get("data"), dict) else it
            slug = (d.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            lm = it.get("updatedAt") or d.get("updatedAt") or it.get("createdAt")
            lastmod = None
            if isinstance(lm, str) and len(lm) >= 10:
                lastmod = lm[:10]  # YYYY-MM-DD
            out.append((slug, lastmod))
    return out


def build_sitemap_xml(*, max_pages: int | None = None) -> bytes:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pages = max_pages if max_pages is not None else MAX_PAGES
    urls = list(_static_urls(now))
    for slug, lastmod in _collect_slugs(pages):
        loc = f"{SITE}/manga/{quote(slug, safe='')}"
        urls.append(
            _url(
                loc,
                changefreq="daily",
                priority="0.8",
                lastmod=lastmod or now,
            )
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return body.encode("utf-8")


def get_sitemap_xml() -> bytes:
    now = time.time()
    if _CACHE["xml"] and now < _CACHE["exp"]:
        return _CACHE["xml"]
    xml = build_sitemap_xml()
    _CACHE["xml"] = xml
    _CACHE["exp"] = now + TTL
    return xml
