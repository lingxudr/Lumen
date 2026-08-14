
"""
Komiku.org engine — WordPress REST + HTML ranking/chapters.

Dipakai oleh KomikuProvider (BaseProvider).
TIDAK download binary image ke disk — itu Image Service.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..base import (
    HTTPError,
    NetworkError,
    ParseError,
    ProviderBlocked,
    TimeoutError,
)
from ..http_client import request_json, request_text

BASE_URL = "https://komiku.org"
REST = f"{BASE_URL}/wp-json/wp/v2"

ENDPOINTS = {
    "manga": "/manga",
    "posts": "/posts",
    "genre": "/genre",
    "series": "/series",
    "statusmanga": "/statusmanga",
    "tipe": "/tipe",
    "media": "/media",
    "comments": "/comments",
}

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

IMAGE_HINTS = ("upload", ".webp", "img.komiku", "image1.komiku")
WM_SKIP = (
    "wmkomiku", "/cover/wm", "/logo", "favicon", "avatar", "/ads",
    "gravatar", "googleads", "doubleclick", "lazy.jpg", "watermark", "banner",
)


class SimpleCache:
    def __init__(self, max_size: int = 200, ttl: int = 3600):
        self.cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        if key not in self.cache:
            self.misses += 1
            return None
        value, expiry = self.cache[key]
        if time.time() > expiry:
            del self.cache[key]
            self.misses += 1
            return None
        self.cache.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        self.cache[key] = (value, time.time() + (ttl if ttl is not None else self.ttl))

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total * 100, 1) if total else 0.0,
        }


class KomikuEngine:
    """Low-level Komiku access (REST + HTML)."""

    def __init__(self, *, cache_ttl: int = 3600, timeout: float = 12.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": BASE_URL + "/",
            }
        )
        self.cache = SimpleCache(ttl=cache_ttl)

    def _ua(self) -> str:
        return random.choice(UA_LIST)

    def _headers(self) -> dict[str, str]:
        h = dict(self.session.headers)
        h["User-Agent"] = self._ua()
        return h

    def get_json(self, path: str, params: dict | None = None) -> Any:
        key = f"json:{path}:{json.dumps(params or {}, sort_keys=True)}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        url = path if path.startswith("http") else f"{REST}{path}"
        data = request_json(
            "komiku",
            "GET",
            url,
            session=self.session,
            headers=self._headers(),
            params=params,
            timeout=self.timeout,
        )
        self.cache.set(key, data)
        return data

    def get_html(self, url: str) -> BeautifulSoup:
        if not url.startswith("http"):
            url = urljoin(BASE_URL, url)
        key = f"html:{url}"
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        text = request_text(
            "komiku",
            url,
            session=self.session,
            headers=self._headers(),
            timeout=self.timeout,
        )
        soup = BeautifulSoup(text, "html.parser")
        self.cache.set(key, soup, ttl=900)
        return soup

    # ---- REST ----
    def latest_manga(self, page: int = 1, per_page: int = 20) -> list[dict]:
        data = self.get_json(
            ENDPOINTS["manga"],
            {
                "orderby": "date",
                "order": "desc",
                "page": page,
                "per_page": min(per_page, 100),
                "status": "publish",
            },
        )
        if not isinstance(data, list):
            return []
        return [self.parse_manga_api(x) for x in data if isinstance(x, dict)]

    def search_manga(self, keyword: str, page: int = 1, per_page: int = 20) -> list[dict]:
        data = self.get_json(
            ENDPOINTS["manga"],
            {
                "search": keyword,
                "page": page,
                "per_page": min(per_page, 100),
                "status": "publish",
            },
        )
        if not isinstance(data, list):
            return []
        return [self.parse_manga_api(x) for x in data if isinstance(x, dict)]

    def manga_by_slug(self, slug: str) -> dict | None:
        data = self.get_json(ENDPOINTS["manga"], {"slug": slug})
        if isinstance(data, list) and data:
            return self.parse_manga_api(data[0])
        return None

    def genres(self) -> list[dict]:
        data = self.get_json(ENDPOINTS["genre"], {"per_page": 100})
        if not isinstance(data, list):
            return []
        return [
            {
                "id": x.get("id"),
                "name": x.get("name"),
                "slug": x.get("slug"),
                "count": x.get("count", 0),
            }
            for x in data
            if isinstance(x, dict)
        ]

    def types(self) -> list[dict]:
        data = self.get_json(ENDPOINTS["tipe"], {"per_page": 100})
        if not isinstance(data, list):
            return []
        return [
            {
                "id": x.get("id"),
                "name": x.get("name"),
                "slug": x.get("slug"),
                "count": x.get("count", 0),
            }
            for x in data
            if isinstance(x, dict)
        ]

    def status_list(self) -> list[dict]:
        data = self.get_json(ENDPOINTS["statusmanga"], {"per_page": 100})
        if not isinstance(data, list):
            return []
        return [
            {
                "id": x.get("id"),
                "name": x.get("name"),
                "slug": x.get("slug"),
                "count": x.get("count", 0),
            }
            for x in data
            if isinstance(x, dict)
        ]

    @staticmethod
    def parse_manga_api(data: dict) -> dict:
        title = data.get("title") or {}
        if isinstance(title, dict):
            title = title.get("rendered") or ""
        excerpt = data.get("excerpt") or {}
        if isinstance(excerpt, dict):
            excerpt = excerpt.get("rendered") or ""
        return {
            "id": data.get("id"),
            "title": title,
            "slug": data.get("slug") or "",
            "link": data.get("link") or "",
            "date": data.get("date") or "",
            "modified": data.get("modified") or "",
            "excerpt": excerpt,
            "genre_ids": data.get("genre") or [],
            "status_ids": data.get("statusmanga") or [],
            "type_ids": data.get("tipe") or [],
            "raw": data,
        }

    # ---- Ranking HTML ----
    def ranking(self, period: str = "mingguan", limit: int = 20) -> list[dict]:
        if period not in ("mingguan", "harian", "total"):
            period = "mingguan"
        soup = self.get_html(BASE_URL + "/")
        panel = soup.find("div", id=f"rank-{period}")
        if not panel:
            return []
        results = []
        for article in panel.select("article.ls4")[:limit]:
            try:
                rank_el = article.find("span", class_="rank-num")
                rank = int(rank_el.get_text(strip=True)) if rank_el else 0
                img = article.find("img")
                cover = ""
                if img:
                    cover = img.get("data-src") or img.get("src") or ""
                    if cover and not cover.startswith("http"):
                        cover = urljoin(BASE_URL, cover)
                link = article.find("a")
                if not link:
                    continue
                title = link.get("title") or ""
                if not title:
                    h4 = article.find("h4")
                    title = h4.get_text(strip=True) if h4 else link.get_text(strip=True)
                href = link.get("href") or ""
                if href and not href.startswith("http"):
                    href = urljoin(BASE_URL, href)
                views_el = article.find("span", class_="ls4s")
                views = views_el.get_text(strip=True) if views_el else ""
                ch_link = article.find("a", class_="ls24")
                latest = ch_link.get_text(strip=True) if ch_link else ""
                results.append(
                    {
                        "rank": rank,
                        "title": title,
                        "url": href,
                        "cover": cover,
                        "views": views,
                        "latest_chapter": latest,
                    }
                )
            except Exception:
                continue
        return results

    # ---- Chapters / pages HTML ----
    def manga_chapters(self, manga_slug: str) -> list[dict]:
        soup = self.get_html(f"{BASE_URL}/manga/{manga_slug}/")
        chapters: list[dict] = []
        table = soup.find("table", id="Daftar_Chapter") or soup.find(
            "table", class_="chapter-table"
        )
        if not table:
            return chapters
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 1:
                continue
            link = cols[0].find("a")
            if not link:
                continue
            title = link.get_text(strip=True)
            href = link.get("href") or ""
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            date = cols[1].get_text(strip=True) if len(cols) > 1 else ""
            num = None
            m = re.search(r"(?:chapter|ch\.?)\s*([0-9]+(?:\.[0-9]+)?)", title, re.I)
            if m:
                try:
                    num = float(m.group(1))
                except ValueError:
                    num = None
            chapters.append(
                {
                    "title": title,
                    "url": href,
                    "date": date,
                    "number": num,
                }
            )
        chapters.reverse()  # newest first
        return chapters

    def chapter_images(self, chapter_url: str) -> list[str]:
        soup = self.get_html(chapter_url)
        images: list[str] = []
        seen: set[str] = set()
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if not src or not any(h in src for h in IMAGE_HINTS):
                continue
            low = src.lower()
            if any(w in low for w in WM_SKIP):
                continue
            if src in seen:
                continue
            seen.add(src)
            images.append(src)
        if not images:
            for tag in soup.find_all(style=True):
                style = tag.get("style") or ""
                for m in re.findall(r"url\([\'\"]?([^\'\"()]+)[\'\"]?\)", style):
                    if any(h in m for h in IMAGE_HINTS) and m not in seen:
                        if not any(w in m.lower() for w in WM_SKIP):
                            seen.add(m)
                            images.append(m)
        return images
