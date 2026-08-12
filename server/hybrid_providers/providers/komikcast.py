"""
Adapter Komikcast (v3) — API https://be.komikcast.cc

Endpoint penting:
  GET /series?page=&take=&sort=updatedAt
  GET /series/{slug|id}
  GET /series/{slug|id}/chapters
  GET /series/{slug}/chapters/{index}   ← images[] untuk playback
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests

from ..base import BaseProvider, ProviderError
from ..models import ChapterInfo, ChapterPages, MangaInfo

API_BASE = "https://be.komikcast.cc"
SITE_BASE = "https://v3.komikcast.fit"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": SITE_BASE,
    "Referer": SITE_BASE + "/",
}

WM_SKIP = (
    "watermark",
    "/logo",
    "favicon",
    "avatar",
    "/ads",
    "googleads",
    "doubleclick",
    "banner",
    "placeholder",
)


def _norm_status(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.lower()
    if "ongoing" in s:
        return "Ongoing"
    if "complete" in s or "end" in s:
        return "Completed"
    if "hiatus" in s:
        return "Hiatus"
    return raw.strip().title()


def _norm_type(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.strip().title()


def _filter_images(images: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in images:
        if not u or not isinstance(u, str):
            continue
        low = u.lower().strip()
        if any(p in low for p in WM_SKIP):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(u.strip())
    return out


class KomikcastProvider(BaseProvider):
    name = "komikcast"
    priority = 10

    def __init__(self, timeout: int = 25):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # cache kecil slug resolution: id -> slug
        self._id_to_slug: dict[str, str] = {}

    def _get_json(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise ProviderError(self.name, f"GET {path} failed", cause=e) from e

    # ------------------------------------------------------------------
    # list / search
    # ------------------------------------------------------------------

    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        # API tidak expose search stabil; ambil newest lalu filter + coba q
        for params in (
            {"q": keyword, "take": limit},
            {"search": keyword, "take": limit},
        ):
            try:
                data = self._get_json("/series", params=params)
                items = data.get("data") or []
                if items:
                    return [self._map_series(it) for it in items[:limit]]
            except ProviderError:
                continue

        # fallback: scan beberapa halaman newest
        kw = keyword.lower().strip()
        found: list[MangaInfo] = []
        for page in range(1, 4):
            try:
                batch = self.get_latest(page=page, limit=30)
            except ProviderError:
                break
            for m in batch:
                blob = f"{m.title} {m.title_alt or ''} {m.slug}".lower()
                if kw in blob:
                    found.append(m)
                    if len(found) >= limit:
                        return found
        return found[:limit]

    def get_latest(self, page: int = 1, limit: int = 20) -> list[MangaInfo]:
        data = self._get_json(
            "/series",
            params={"page": page, "take": limit, "sort": "updatedAt"},
        )
        items = data.get("data") or []
        return [self._map_series(it) for it in items]

    def get_manga(self, source_slug: str) -> MangaInfo | None:
        try:
            data = self._get_json(
                f"/series/{quote(str(source_slug), safe='')}",
                params={"includeMeta": "true"},
            )
        except ProviderError:
            return None
        item = data.get("data")
        if not item:
            return None
        return self._map_series(item)

    # ------------------------------------------------------------------
    # chapters
    # ------------------------------------------------------------------

    def get_chapters(self, source_slug: str) -> list[ChapterInfo]:
        """
        source_slug: slug series ATAU numeric id.
        Untuk playback, URL chapter diset ke path API:
          /series/{slug}/chapters/{index}
        """
        series_key = str(source_slug)
        slug = series_key

        # resolve id → slug jika perlu
        if series_key.isdigit():
            info = self.get_manga(series_key)
            if not info:
                raise ProviderError(self.name, f"series tidak ditemukan: {source_slug}")
            slug = info.slug
            series_key = info.source_id or series_key
            self._id_to_slug[str(series_key)] = slug
        else:
            # pastikan slug valid & cache id
            info = self.get_manga(slug)
            if info and info.source_id:
                self._id_to_slug[str(info.source_id)] = slug

        data = self._get_json(f"/series/{quote(str(source_slug), safe='')}/chapters")
        items = data.get("data") or []

        chapters: list[ChapterInfo] = []
        for ch in items:
            d = ch.get("data") or {}
            idx = d.get("index")
            if idx is None:
                # kadang di root
                idx = ch.get("chapterIndex")
            number = float(idx) if idx is not None else None
            name = d.get("title") or (
                f"Chapter {idx}" if idx is not None else "Chapter"
            )
            ch_id = str(ch.get("id")) if ch.get("id") is not None else None

            # API images path (paling penting untuk get_pages)
            # frontend pakai slug + index, bukan chapter id
            api_path = None
            site_url = None
            if number is not None and slug:
                # index bisa 243 atau 243.0 → API pakai int jika bulat
                idx_path = int(number) if float(number).is_integer() else number
                api_path = f"{API_BASE}/series/{slug}/chapters/{idx_path}"
                site_url = f"{SITE_BASE}/series/{slug}/chapter/{idx_path}"

            chapters.append(
                ChapterInfo(
                    number=number,
                    name=str(name),
                    url=api_path or site_url,
                    source_chapter_id=ch_id,
                    published_at=ch.get("createdAt") or ch.get("updatedAt"),
                    provider=self.name,
                )
            )

        chapters.sort(
            key=lambda c: (c.number is not None, c.number or 0),
            reverse=True,
        )
        return chapters

    # ------------------------------------------------------------------
    # pages (playback) — FIXED
    # ------------------------------------------------------------------

    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        """
        Ambil images[] dari:
          GET /series/{slug}/chapters/{index}

        chapter.url sebaiknya sudah diisi get_chapters (API URL).
        Fallback: parse number + slug dari url site.
        """
        api_url = self._resolve_chapter_api_url(chapter)
        if not api_url:
            raise ProviderError(
                self.name,
                "tidak bisa resolve API chapter URL (butuh series slug + index)",
            )

        try:
            r = self.session.get(api_url, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise ProviderError(self.name, f"gagal fetch pages: {api_url}", cause=e) from e

        data = payload.get("data") or {}
        # nested: data.data.images
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        images_raw = []
        if isinstance(inner, dict):
            images_raw = inner.get("images") or []
        if not images_raw and isinstance(data, dict):
            images_raw = data.get("images") or []

        if not isinstance(images_raw, list):
            images_raw = []

        images = _filter_images([str(u) for u in images_raw if u])
        if not images:
            raise ProviderError(self.name, f"images kosong: {api_url}")

        return ChapterPages(
            images=images,
            provider=self.name,
            chapter_number=chapter.number,
            chapter_name=chapter.name,
            source_url=api_url,
        )

    def _resolve_chapter_api_url(self, chapter: ChapterInfo) -> str | None:
        url = (chapter.url or "").strip()
        # sudah API url
        if url.startswith(API_BASE) and "/chapters/" in url:
            return url

        # site url: /series/{slug}/chapter/{index}
        m = re.search(
            r"/series/([^/]+)/chapter/([0-9]+(?:\.[0-9]+)?)",
            url,
        )
        if m:
            slug, idx = m.group(1), m.group(2)
            return f"{API_BASE}/series/{slug}/chapters/{idx}"

        # hanya punya number — tidak cukup tanpa slug
        if chapter.number is not None and url:
            # coba anggap url adalah slug series
            if "://" not in url and "/" not in url:
                idx = (
                    int(chapter.number)
                    if float(chapter.number).is_integer()
                    else chapter.number
                )
                return f"{API_BASE}/series/{url}/chapters/{idx}"

        return None

    # ------------------------------------------------------------------

    def _map_series(self, item: dict[str, Any]) -> MangaInfo:
        if "data" in item and isinstance(item["data"], dict):
            sid = item.get("id")
            d = item["data"]
        else:
            sid = item.get("id")
            d = item

        slug = d.get("slug") or str(sid or "")
        title = d.get("title") or slug
        cover = d.get("coverImage") or d.get("cover") or None

        genres: list[str] = []
        if isinstance(d.get("genres"), list):
            for g in d["genres"]:
                if isinstance(g, str):
                    genres.append(g)
                elif isinstance(g, dict) and g.get("name"):
                    genres.append(str(g["name"]))

        rating = d.get("rating")
        try:
            rating = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None

        if sid is not None and slug:
            self._id_to_slug[str(sid)] = slug

        return MangaInfo(
            slug=slug,
            title=title,
            title_alt=d.get("nativeTitle"),
            synopsis=d.get("synopsis") or d.get("description"),
            cover_url=cover,
            author=d.get("author"),
            status=_norm_status(d.get("status")),
            type=_norm_type(d.get("format") or d.get("type")),
            genres=genres,
            rating=rating,
            source_slug=slug,
            source_id=str(sid) if sid is not None else None,
            source_url=f"{SITE_BASE}/series/{slug}" if slug else None,
            provider=self.name,
            raw=item,
        )
