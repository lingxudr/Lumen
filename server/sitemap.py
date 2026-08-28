"""
SEO-optimized dynamic sitemaps for Lumen.

Endpoints:
  GET /sitemap.xml              → sitemap index (best for Google)
  GET /api/sitemap              → same as index
  GET /sitemap-pages.xml        → static hub pages
  GET /sitemap-manga.xml        → manga detail URLs (updates + popular + …)
  GET /sitemap-images.xml       → Google image sitemap (covers)
  GET /api/sitemap/manga|pages|images

Env:
  SITEMAP_SITE=https://www.v1lumen.my.id
  SITEMAP_PAGES=8               # update pages (~30 each)
  SITEMAP_TTL=3600
  SITEMAP_MAX_URLS=5000         # hard cap per file
"""
from __future__ import annotations

import os
import re
import time
import xml.sax.saxutils as sax
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

_CACHE: dict[str, Any] = {}

SITE = (
    os.environ.get("SITEMAP_SITE")
    or os.environ.get("PUBLIC_SITE")
    or "https://www.v1lumen.my.id"
).rstrip("/")
TTL = float(os.environ.get("SITEMAP_TTL", "3600"))
MAX_PAGES = int(os.environ.get("SITEMAP_PAGES", "8"))
MAX_URLS = int(os.environ.get("SITEMAP_MAX_URLS", "5000"))

_SLUG_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", re.I)


def _esc(s: str) -> str:
    return sax.escape((s or "").strip())


def _iso_day(value: Any) -> str | None:
    """Normalize to YYYY-MM-DD for <lastmod>."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return None
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _vt():
    try:
        from providers import voratoon as vt

        return vt
    except Exception:
        try:
            from server.providers import voratoon as vt  # type: ignore

            return vt
        except Exception as e:
            print("sitemap voratoon import:", e, flush=True)
            return None


def _item_fields(it: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """slug, lastmod, cover, title"""
    if not isinstance(it, dict):
        return None, None, None, None
    d = it.get("data") if isinstance(it.get("data"), dict) else it
    if not isinstance(d, dict):
        return None, None, None, None
    slug = (d.get("slug") or "").strip().strip("/")
    if not slug or not _SLUG_OK.match(slug) or len(slug) > 120:
        return None, None, None, None
    lm = _iso_day(it.get("updatedAt") or d.get("updatedAt") or it.get("createdAt") or d.get("createdAt"))
    cover = (
        d.get("coverImage")
        or d.get("cover")
        or d.get("backgroundImage")
        or ""
    )
    if isinstance(cover, str):
        cover = cover.strip()
    else:
        cover = ""
    if cover and not cover.startswith("http"):
        cover = ""
    title = (d.get("title") or slug).strip()
    return slug, lm, cover or None, title


def _collect_manga() -> list[dict[str, str | None]]:
    """
    Deduped manga rows: slug, lastmod, cover, title, priority_hint.
    Sources: updates (multi-page), popular, newSeries, completed.
    """
    vt = _vt()
    if vt is None:
        return []

    by_slug: dict[str, dict[str, Any]] = {}

    def add_list(items: list, *, prio: float) -> None:
        for it in items or []:
            slug, lm, cover, title = _item_fields(it if isinstance(it, dict) else {})
            if not slug:
                continue
            row = by_slug.get(slug)
            if row is None:
                by_slug[slug] = {
                    "slug": slug,
                    "lastmod": lm,
                    "cover": cover,
                    "title": title,
                    "prio": prio,
                }
            else:
                if lm and (not row["lastmod"] or lm > row["lastmod"]):
                    row["lastmod"] = lm
                if cover and not row["cover"]:
                    row["cover"] = cover
                if title and title != slug:
                    row["title"] = title
                row["prio"] = max(float(row["prio"]), prio)

    # 1) Fresh updates — highest SEO value
    for page in range(1, max(1, MAX_PAGES) + 1):
        try:
            payload = vt.fetch_updates_html(page=page)
            items = payload.get("data") or []
            if not items:
                break
            # Newer pages slightly higher priority
            prio = 0.9 if page == 1 else max(0.55, 0.85 - page * 0.03)
            add_list(items, prio=prio)
        except Exception as e:
            print(f"sitemap updates p{page}:", e, flush=True)
            break
        if len(by_slug) >= MAX_URLS:
            break

    # 2) Home feeds: popular / new / completed
    try:
        home = vt.fetch_home_rsc()
        data = home.get("data") if isinstance(home.get("data"), dict) else home
        if isinstance(data, dict):
            add_list(data.get("popular") or [], prio=0.88)
            add_list(data.get("newSeries") or [], prio=0.82)
            add_list(data.get("completed") or [], prio=0.72)
            add_list(data.get("updates") or [], prio=0.86)
    except Exception as e:
        print("sitemap home rsc:", e, flush=True)

    rows = list(by_slug.values())
    # Sort: higher prio first, then lastmod desc
    rows.sort(
        key=lambda r: (float(r.get("prio") or 0), r.get("lastmod") or ""),
        reverse=True,
    )
    return rows[:MAX_URLS]


def _url_entry(
    loc: str,
    *,
    lastmod: str | None = None,
    changefreq: str = "daily",
    priority: str = "0.5",
) -> str:
    lines = ["  <url>", f"    <loc>{_esc(loc)}</loc>"]
    if lastmod:
        lines.append(f"    <lastmod>{_esc(lastmod)}</lastmod>")
    lines.append(f"    <changefreq>{_esc(changefreq)}</changefreq>")
    lines.append(f"    <priority>{_esc(priority)}</priority>")
    lines.append("  </url>")
    return "\n".join(lines)


def _image_url_entry(
    page_loc: str,
    *,
    image_loc: str,
    title: str | None = None,
    lastmod: str | None = None,
) -> str:
    lines = ["  <url>", f"    <loc>{_esc(page_loc)}</loc>"]
    if lastmod:
        lines.append(f"    <lastmod>{_esc(lastmod)}</lastmod>")
    lines.append("    <image:image>")
    lines.append(f"      <image:loc>{_esc(image_loc)}</image:loc>")
    if title:
        lines.append(f"      <image:title>{_esc(title[:200])}</image:title>")
    lines.append("    </image:image>")
    lines.append("  </url>")
    return "\n".join(lines)


def build_pages_xml() -> bytes:
    today = _today()
    entries = [
        _url_entry(f"{SITE}/", lastmod=today, changefreq="hourly", priority="1.0"),
        _url_entry(f"{SITE}/latest", lastmod=today, changefreq="hourly", priority="0.95"),
        _url_entry(f"{SITE}/popular", lastmod=today, changefreq="daily", priority="0.9"),
        _url_entry(f"{SITE}/latest?tab=completed", lastmod=today, changefreq="daily", priority="0.75"),
        _url_entry(f"{SITE}/latest?tab=new_series", lastmod=today, changefreq="daily", priority="0.75"),
        _url_entry(f"{SITE}/latest?tab=browse", lastmod=today, changefreq="daily", priority="0.7"),
        _url_entry(f"{SITE}/search", lastmod=today, changefreq="weekly", priority="0.4"),
        _url_entry(f"{SITE}/lumenrest/docs", lastmod=today, changefreq="monthly", priority="0.2"),
    ]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return body.encode("utf-8")


def build_manga_xml() -> bytes:
    today = _today()
    rows = _collect_manga()
    entries = []
    for r in rows:
        slug = r["slug"]
        loc = f"{SITE}/manga/{quote(str(slug), safe='')}"
        prio = float(r.get("prio") or 0.7)
        entries.append(
            _url_entry(
                loc,
                lastmod=r.get("lastmod") or today,
                changefreq="daily",
                priority=f"{min(0.95, max(0.5, prio)):.2f}",
            )
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return body.encode("utf-8")


def build_images_xml() -> bytes:
    today = _today()
    rows = _collect_manga()
    entries = []
    for r in rows:
        cover = r.get("cover")
        if not cover:
            continue
        slug = r["slug"]
        page = f"{SITE}/manga/{quote(str(slug), safe='')}"
        entries.append(
            _image_url_entry(
                page,
                image_loc=str(cover),
                title=r.get("title"),
                lastmod=r.get("lastmod") or today,
            )
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return body.encode("utf-8")


def build_index_xml() -> bytes:
    today = _today()
    # Sitemap index — Google discovers children
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <sitemap>",
        f"    <loc>{_esc(SITE + '/sitemap-pages.xml')}</loc>",
        f"    <lastmod>{today}</lastmod>",
        "  </sitemap>",
        "  <sitemap>",
        f"    <loc>{_esc(SITE + '/sitemap-manga.xml')}</loc>",
        f"    <lastmod>{today}</lastmod>",
        "  </sitemap>",
        "  <sitemap>",
        f"    <loc>{_esc(SITE + '/sitemap-images.xml')}</loc>",
        f"    <lastmod>{today}</lastmod>",
        "  </sitemap>",
        "</sitemapindex>",
        "",
    ]
    return "\n".join(parts).encode("utf-8")


def _cached(key: str, builder) -> bytes:
    now = time.time()
    row = _CACHE.get(key)
    if row and now < row[0]:
        return row[1]
    data = builder()
    _CACHE[key] = (now + TTL, data)
    return data


def get_sitemap_index() -> bytes:
    return _cached("index", build_index_xml)


def get_sitemap_pages() -> bytes:
    return _cached("pages", build_pages_xml)


def get_sitemap_manga() -> bytes:
    return _cached("manga", build_manga_xml)


def get_sitemap_images() -> bytes:
    return _cached("images", build_images_xml)


# Back-compat: main endpoint serves INDEX (SEO best practice)
def get_sitemap_xml() -> bytes:
    return get_sitemap_index()
