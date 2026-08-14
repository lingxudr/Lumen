"""
Adapter Komiku (komiku.org) — BaseProvider.

Engine: komiku_scraper.KomikuEngine (REST + ranking HTML + chapters/pages).
- Catalog/search/latest → WP REST
- Ranking → HTML panels rank-mingguan/harian/total
- Chapters/pages → HTML (URL only; no image download)
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from ..base import ALL_CAPABILITIES, BaseProvider, ProviderError
from ..models import ChapterInfo, ChapterPages, MangaInfo
from .komiku_scraper import KomikuEngine

BASE_SITE = "https://komiku.org"
BASE_MIRRORS = [
    "https://komiku.org",
    "https://komiku.id",
    "https://api.komiku.org",
]
REST_BASE = f"{BASE_SITE}/wp-json/wp/v2"

def _proxy_base() -> str:
    """Optional Vercel/other proxy to bypass Railway IP ban on Komiku."""
    import os
    return (os.environ.get("KOMIKU_PROXY_BASE") or "").rstrip("/")

def _via_proxy(absolute_url: str) -> str:
    """
    Map https://komiku.org/wp-json/... → {PROXY}/api/komiku/wp-json/...
    """
    pb = _proxy_base()
    if not pb:
        return absolute_url
    from urllib.parse import urlparse
    u = urlparse(absolute_url)
    path = u.path or "/"
    q = ("?" + u.query) if u.query else ""
    # proxy expects /api/komiku{path}
    if pb.endswith("/api/komiku"):
        return f"{pb}{path}{q}"
    return f"{pb}/api/komiku{path}{q}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE_SITE + "/",
}

WM_SKIP = (
    "wmkomiku",
    "/cover/wm",
    "/logo",
    "favicon",
    "avatar",
    "/ads",
    "gravatar",
    "googleads",
    "doubleclick",
    "lazy.jpg",
    "watermark",
    "banner",
)

# Taxonomy cache
_TAX_TTL = 24 * 3600          # fresh window
_TAX_STALE_TTL = 7 * 24 * 3600  # boleh pakai stale sambil revalidate
_TAX_NAMES = ("genre", "tipe", "statusmanga", "ratemanga", "genreutama")
_DEFAULT_TAX_DIR = Path(__file__).resolve().parents[1] / ".cache"

def _abs(url: str | None) -> str | None:
    if not url or str(url).startswith("data:"):
        return None
    return urljoin(BASE_SITE, str(url).strip())


def _clean_cover(url: str | None) -> str | None:
    u = _abs(url)
    if not u:
        return None
    if "lazy.jpg" in u or "/asset/img/lazy" in u:
        return None
    return u


def _strip_title(title: str | None) -> str | None:
    if not title:
        return None
    t = html.unescape(str(title)).strip()
    t = re.sub(r"^Komik\s*", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip().replace("→", "").strip()
    return t or None


def _parse_status(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.lower()
    if "ongoing" in s or "on going" in s or "on-going" in s or "berjalan" in s:
        return "Ongoing"
    if "end" in s or "complete" in s or "tamat" in s:
        return "Completed"
    if "hiatus" in s:
        return "Hiatus"
    return raw.strip()


def _chapter_number(name: str | None) -> float | None:
    if not name:
        return None
    m = re.search(r"(?:chapter|ch\.?)\s*([0-9]+(?:\.[0-9]+)?)", name, re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _slug_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/manga/")
    if len(parts) > 1:
        return parts[-1].split("/")[0]
    return url.rstrip("/").split("/")[-1]


def filter_watermark(images: list[str]) -> list[str]:
    """Skip WM di posisi mana pun."""
    out: list[str] = []
    for u in images:
        low = u.lower()
        if any(p in low for p in WM_SKIP):
            continue
        if "/cover/" in low and "/upload" not in low:
            continue
        out.append(u)
    return out


class KomikuProvider(BaseProvider):
    capabilities = ALL_CAPABILITIES
    name = "komiku"
    priority = 20

    def __init__(
        self,
        timeout: int = 25,
        tax_cache_dir: str | Path | None = None,
        tax_ttl: int = _TAX_TTL,
    ):
        self.timeout = timeout
        self.tax_ttl = tax_ttl
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # cache: tax_name -> {id: label}
        self._tax_maps: dict[str, dict[int, str]] = {}
        self._tax_meta: dict[str, float] = {}  # tax -> loaded_at epoch
        self._tax_lock = threading.Lock()
        self._tax_dir = Path(tax_cache_dir) if tax_cache_dir else _DEFAULT_TAX_DIR
        self._tax_dir.mkdir(parents=True, exist_ok=True)
        # warm dari disk (tanpa network)
        self.engine = KomikuEngine(timeout=float(self.timeout))
        self._load_tax_disk_all()
    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get_html(self, url: str, **kwargs: Any) -> str:
        urls = [url]
        # swap host across mirrors when url is on komiku family
        for base in BASE_MIRRORS:
            for host in ("https://komiku.org", "https://komiku.id", "https://api.komiku.org"):
                if host in url and base != host:
                    urls.append(url.replace(host, base))
        last_err: Exception | None = None
        seen: set[str] = set()
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            try:
                fetch_url = _via_proxy(u) if _proxy_base() else u
                r = self.session.get(
                    fetch_url,
                    timeout=self.timeout,
                    headers={**HEADERS, "Accept": "text/html,*/*", "Referer": "https://komiku.org/"},
                    allow_redirects=True,
                    **kwargs,
                )
                r.raise_for_status()
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except Exception as e:
                last_err = e
                continue
        # last: try proxy-only for original url
        if _proxy_base():
            try:
                fetch_url = _via_proxy(url)
                r = self.session.get(
                    fetch_url,
                    timeout=self.timeout,
                    headers={**HEADERS, "Accept": "text/html,*/*", "Referer": "https://komiku.org/"},
                    **kwargs,
                )
                r.raise_for_status()
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except Exception as e:
                last_err = e
        raise ProviderError(self.name, f"GET HTML failed: {url}", cause=last_err) from last_err

    def _get_json(self, path: str, params: dict | None = None) -> Any:
        if path.startswith("http"):
            candidates = [path]
        else:
            candidates = [f"{base}/wp-json/wp/v2{path}" for base in BASE_MIRRORS]
        # Prefer proxy first when configured (Railway ban bypass)
        if _proxy_base():
            proxied = []
            for u in candidates:
                proxied.append(_via_proxy(u))
            candidates = proxied + candidates
        last_err: Exception | None = None
        for url in candidates:
            try:
                r = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    headers={
                        **HEADERS,
                        "Accept": "application/json",
                        "Referer": "https://komiku.org/",
                    },
                    allow_redirects=True,
                )
                r.raise_for_status()
                return r.json(), r.headers
            except Exception as e:
                last_err = e
                print(f"komiku mirror fail {url}: {e}", flush=True)
                continue
        raise ProviderError(
            self.name, f"GET JSON failed: {path}", cause=last_err
        ) from last_err

    # ------------------------------------------------------------------
    # Taxonomy map (lazy + disk + stale-while-revalidate)
    # ------------------------------------------------------------------

    def _tax_path(self, tax: str) -> Path:
        return self._tax_dir / f"komiku_tax_{tax}.json"

    def _load_tax_disk(self, tax: str) -> tuple[dict[int, str], float] | None:
        path = self._tax_path(tax)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            loaded_at = float(payload.get("loaded_at") or 0)
            raw = payload.get("map") or {}
            mapping = {int(k): str(v) for k, v in raw.items()}
            return mapping, loaded_at
        except Exception:
            return None

    def _save_tax_disk(self, tax: str, mapping: dict[int, str], loaded_at: float) -> None:
        path = self._tax_path(tax)
        try:
            payload = {
                "tax": tax,
                "loaded_at": loaded_at,
                "count": len(mapping),
                "map": {str(k): v for k, v in mapping.items()},
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    def _load_tax_disk_all(self) -> None:
        """Warm memory dari disk tanpa network."""
        now = time.time()
        for tax in _TAX_NAMES:
            disk = self._load_tax_disk(tax)
            if not disk:
                continue
            mapping, loaded_at = disk
            # terima selama masih dalam stale window
            if mapping and (now - loaded_at) <= _TAX_STALE_TTL:
                self._tax_maps[tax] = mapping
                self._tax_meta[tax] = loaded_at

    def _fetch_tax_network(self, tax: str) -> dict[int, str]:
        """Fetch satu taxonomy; payload minimal (_fields=id,name)."""
        mapping: dict[int, str] = {}
        page = 1
        while page <= 20:
            data, headers = self._get_json(
                f"/{tax}",
                params={
                    "per_page": 100,
                    "page": page,
                    "_fields": "id,name,slug",
                },
            )
            if not isinstance(data, list) or not data:
                break
            for item in data:
                try:
                    tid = int(item["id"])
                    label = html.unescape(
                        str(item.get("name") or item.get("slug") or "")
                    ).strip()
                    if label:
                        mapping[tid] = label
                except Exception:
                    continue
            total_pages = int(headers.get("X-WP-TotalPages") or 1)
            if page >= total_pages:
                break
            page += 1
        return mapping

    def _ensure_taxonomy(self, tax: str) -> dict[int, str]:
        """
        Lazy-load per taxonomy:
        1. memory fresh → pakai
        2. memory/disk stale → pakai dulu, revalidate network di background-ish (sync ringan)
        3. kosong → fetch network
        """
        now = time.time()
        with self._tax_lock:
            mem = self._tax_maps.get(tax)
            loaded_at = self._tax_meta.get(tax, 0.0)

            if mem and (now - loaded_at) < self.tax_ttl:
                return mem

            # coba disk jika memory kosong
            if not mem:
                disk = self._load_tax_disk(tax)
                if disk:
                    mapping, disk_at = disk
                    if mapping and (now - disk_at) <= _TAX_STALE_TTL:
                        self._tax_maps[tax] = mapping
                        self._tax_meta[tax] = disk_at
                        mem = mapping
                        loaded_at = disk_at
                        # masih fresh di disk?
                        if (now - disk_at) < self.tax_ttl:
                            return mem

            # butuh network (kosong atau expired)
            need_network = (not mem) or ((now - loaded_at) >= self.tax_ttl)
            if need_network:
                try:
                    mapping = self._fetch_tax_network(tax)
                    if mapping:
                        self._tax_maps[tax] = mapping
                        self._tax_meta[tax] = now
                        self._save_tax_disk(tax, mapping, now)
                        return mapping
                except ProviderError:
                    # fallback: pakai stale memory/disk jika ada
                    if mem:
                        return mem
                    return {}

            return mem or {}

    def _ensure_taxonomies(self) -> None:
        """Opsional: warm semua taxonomy (dipakai jarang)."""
        for tax in _TAX_NAMES:
            self._ensure_taxonomy(tax)

    def _map_ids(self, tax: str, ids: list[Any] | None) -> list[str]:
        if not ids:
            return []
        mapping = self._ensure_taxonomy(tax)
        out: list[str] = []
        for i in ids:
            try:
                name = mapping.get(int(i))
            except (TypeError, ValueError):
                continue
            if name and name not in out:
                out.append(name)
        return out

    def _status_from_ids(self, ids: list[Any] | None, class_list: list[str] | None = None) -> str | None:
        names = self._map_ids("statusmanga", ids)
        for n in names:
            parsed = _parse_status(n)
            if parsed:
                return parsed
        # fallback class_list e.g. statusmanga-ongoing
        if class_list:
            for c in class_list:
                if "statusmanga-" in c or c in ("ongoing", "completed", "hiatus"):
                    parsed = _parse_status(c.replace("statusmanga-", ""))
                    if parsed:
                        return parsed
        return None

    def _type_from_ids(self, ids: list[Any] | None, class_list: list[str] | None = None) -> str | None:
        names = self._map_ids("tipe", ids)
        for n in names:
            low = n.lower()
            if low in {"chapter"}:
                continue
            return n
        if class_list:
            for c in class_list:
                if c.startswith("tipe-"):
                    t = c.replace("tipe-", "").title()
                    if t.lower() != "chapter":
                        return t
        return None

    # ------------------------------------------------------------------
    # REST → MangaInfo
    # ------------------------------------------------------------------

    def _map_rest_manga(self, item: dict[str, Any]) -> MangaInfo:
        self._ensure_taxonomies()
        slug = item.get("slug") or ""
        title_raw = item.get("title")
        if isinstance(title_raw, dict):
            title_raw = title_raw.get("rendered")
        title = _strip_title(title_raw) or slug

        class_list = item.get("class_list") or []
        genres = self._map_ids("genre", item.get("genre"))
        if not genres:
            # fallback class_list genre-xxx
            for c in class_list:
                if c.startswith("genre-"):
                    g = c.replace("genre-", "").replace("-", " ").title()
                    if g and g not in genres:
                        genres.append(g)

        status = self._status_from_ids(item.get("statusmanga"), class_list)
        mtype = self._type_from_ids(item.get("tipe"), class_list)

        # rating: taxonomy ratemanga name sering angka
        rating = None
        rate_names = self._map_ids("ratemanga", item.get("ratemanga"))
        for rn in rate_names:
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", rn)
            if m:
                rating = float(m.group(1))
                break

        link = item.get("link") or (f"{BASE_SITE}/manga/{slug}/" if slug else None)
        # rewrite secure.komikid.org → komiku.org jika muncul
        if link and "komikid.org" in link:
            link = link.replace("https://secure.komikid.org", BASE_SITE).replace(
                "http://secure.komikid.org", BASE_SITE
            )

        return MangaInfo(
            slug=slug,
            title=title,
            title_alt=None,
            synopsis=None,  # REST tidak expose; diisi get_manga HTML
            cover_url=None,  # diisi HTML / list HTML
            author=None,
            status=status,
            type=mtype,
            genres=genres,
            rating=rating,
            source_slug=slug,
            source_id=str(item["id"]) if item.get("id") is not None else None,
            source_url=link,
            provider=self.name,
            raw={"rest": item},
        )

    def _rest_manga_list(self, params: dict[str, Any]) -> list[MangaInfo]:
        data, _headers = self._get_json("/manga", params=params)
        if not isinstance(data, list):
            return []
        return [self._map_rest_manga(it) for it in data if isinstance(it, dict)]

    # ------------------------------------------------------------------
    # search / latest (REST dulu, fallback HTML)
    # ------------------------------------------------------------------


    # ---- ranking (HTML panels) ----
    def get_ranking(self, period: str = "mingguan", limit: int = 20) -> list[MangaInfo]:
        """Popular via homepage rank panel; fallback REST modified."""
        try:
            rows = self.engine.ranking(period=period, limit=limit)
        except Exception:
            rows = []
        out: list[MangaInfo] = []
        for r in rows:
            slug = _slug_from_url(r.get("url") or "")
            if not slug:
                continue
            out.append(
                MangaInfo(
                    slug=slug,
                    title=_strip_title(r.get("title")) or slug,
                    cover_url=_clean_cover(r.get("cover")),
                    latest_chapter=r.get("latest_chapter"),
                    updated_label=r.get("views"),
                    source_slug=slug,
                    source_url=r.get("url"),
                    provider=self.name,
                    raw=r,
                )
            )
        if out:
            return out
        return self.get_latest(limit=limit, page=1)

    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        per_page = min(max(limit, 1), 100)
        try:
            items = self._rest_manga_list(
                {"search": keyword, "per_page": per_page, "orderby": "relevance"}
            )
            if items:
                return items[:limit]
        except ProviderError:
            pass
        # fallback HTML
        return self._search_html(keyword, limit=limit)

    def get_latest(self, limit: int = 20, page: int = 1) -> list[MangaInfo]:
        """
        REST-first (andal di server yang HTML di-block/403).
        HTML merge opsional; gagal HTML tidak menggugurkan REST.
        """
        rest_items: list[MangaInfo] = []
        rest_by_slug: dict[str, MangaInfo] = {}
        try:
            rest_items = self._rest_manga_list(
                {
                    "orderby": "modified",
                    "order": "desc",
                    "per_page": min(max(limit, 1), 100),
                    "page": max(page, 1),
                }
            )
            for m in rest_items:
                if m.slug:
                    rest_by_slug[m.slug] = m
        except Exception as e:
            print("komiku get_latest REST:", e, flush=True)
            rest_items = []

        # Optional HTML enrich — never required
        if page <= 1 and rest_items:
            try:
                html_items = self._latest_terbaru_html(limit=max(limit, 20))
            except Exception:
                html_items = []
            if html_items:
                merged: list[MangaInfo] = []
                for h in html_items:
                    r = rest_by_slug.get(h.slug)
                    if r:
                        merged.append(
                            MangaInfo(
                                slug=h.slug,
                                title=h.title or r.title,
                                title_alt=r.title_alt,
                                synopsis=r.synopsis,
                                cover_url=h.cover_url or r.cover_url,
                                author=r.author,
                                status=r.status,
                                type=r.type,
                                genres=r.genres or h.genres,
                                rating=r.rating,
                                latest_chapter=h.latest_chapter
                                or (h.raw or {}).get("latest_chapter"),
                                latest_chapter_url=h.latest_chapter_url
                                or (h.raw or {}).get("latest_chapter_url"),
                                updated_label=h.updated_label
                                or (h.raw or {}).get("updated_label"),
                                source_slug=h.slug,
                                source_id=r.source_id,
                                source_url=h.source_url or r.source_url,
                                provider=self.name,
                                raw={**(r.raw or {}), **(h.raw or {})},
                            )
                        )
                    else:
                        h.latest_chapter = h.latest_chapter or (h.raw or {}).get(
                            "latest_chapter"
                        )
                        h.updated_label = h.updated_label or (h.raw or {}).get(
                            "updated_label"
                        )
                        merged.append(h)
                    if len(merged) >= limit:
                        break
                if merged:
                    return merged[:limit]

        if rest_items:
            return rest_items[:limit]

        # last resort HTML only
        try:
            return self._latest_html(limit=limit)
        except Exception as e:
            print("komiku get_latest HTML fallback failed:", e, flush=True)
            return []

    def _latest_terbaru_html(self, limit: int = 20) -> list[MangaInfo]:
        """Parse blok #Terbaru di beranda Komiku (sumber update yang user lihat)."""
        html_text = self._get_html(BASE_SITE + "/")
        soup = BeautifulSoup(html_text, "html.parser")
        root = soup.select_one("#Terbaru") or soup
        out: list[MangaInfo] = []
        seen: set[str] = set()

        # tiap item punya .ls2j (judul + chapter) berdampingan .ls2v (cover)
        blocks = root.select("div.ls2j")
        for block in blocks:
            a = block.select_one("h3 a[href*='/manga/'], a[href*='/manga/']")
            if not a:
                continue
            href = a.get("href") or ""
            url = _abs(href)
            slug = _slug_from_url(url or href)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            title = _strip_title(a.get_text(strip=True)) or slug

            time_el = block.select_one("span.ls2t")
            time_label = time_el.get_text(" ", strip=True) if time_el else None

            ch_a = block.select_one("a.ls2l")
            ch_name = ch_a.get_text(strip=True) if ch_a else None
            ch_url = _abs(ch_a.get("href")) if ch_a and ch_a.get("href") else None

            # cover dari sibling ls2v sebelumnya
            cover = None
            prev = block.find_previous_sibling("div", class_=lambda c: c and "ls2v" in c)
            if prev:
                img = prev.select_one("img")
                if img:
                    cover = _clean_cover(img.get("data-src") or img.get("src"))

            # genre kasar dari time label "Fantasi · 3 menit lalu"
            genres: list[str] = []
            if time_label and "·" in time_label:
                g = time_label.split("·")[0].strip()
                if g and not any(x in g.lower() for x in ("menit", "jam", "detik", "hari")):
                    genres = [g]

            info = MangaInfo(
                slug=slug,
                title=title,
                cover_url=cover,
                genres=genres,
                latest_chapter=ch_name,
                latest_chapter_url=ch_url,
                updated_label=time_label,
                source_slug=slug,
                source_url=url,
                provider=self.name,
                raw={
                    "latest_chapter": ch_name,
                    "latest_chapter_url": ch_url,
                    "updated_label": time_label,
                    "source": "html_terbaru",
                },
            )
            out.append(info)
            if len(out) >= limit:
                break

        return out

    def _search_html(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        url = f"{BASE_SITE}/?post_type=manga&s={quote_plus(keyword)}"
        html_text = self._get_html(url)
        soup = BeautifulSoup(html_text, "html.parser")
        items = self._parse_cards(soup)
        return items[:limit]

    def _latest_html(self, limit: int = 20) -> list[MangaInfo]:
        html_text = self._get_html(BASE_SITE + "/")
        soup = BeautifulSoup(html_text, "html.parser")
        items = self._parse_home(soup) or self._parse_cards(soup)
        return items[:limit]

    # ------------------------------------------------------------------
    # HTML card parsers (fallback + cover)
    # ------------------------------------------------------------------

    def _parse_cards(self, soup: BeautifulSoup) -> list[MangaInfo]:
        out: list[MangaInfo] = []
        seen: set[str] = set()
        for card in soup.select("article.manga-card"):
            a = card.select_one("a[href*='/manga/']")
            if not a:
                continue
            href = a.get("href") or ""
            url = _abs(href)
            slug = _slug_from_url(url or href)
            if slug in seen:
                continue
            seen.add(slug)
            img = card.select_one("img")
            cover = _clean_cover(img.get("data-src") or img.get("src")) if img else None
            title = _strip_title(img.get("alt") if img else None) or _strip_title(
                a.get_text(" ", strip=True)
            )
            if not title:
                continue
            out.append(
                MangaInfo(
                    slug=slug,
                    title=title,
                    cover_url=cover,
                    source_slug=slug,
                    source_url=url,
                    provider=self.name,
                )
            )
        return out

    def _parse_home(self, soup: BeautifulSoup) -> list[MangaInfo]:
        out: list[MangaInfo] = []
        seen: set[str] = set()
        for h3 in soup.select("h3"):
            title = _strip_title(h3.get_text(strip=True))
            if not title or title.lower() in {"history", "bookmark"}:
                continue
            box = h3
            img = link = None
            for _ in range(6):
                box = box.parent
                if not box:
                    break
                if not link:
                    link = box.select_one("a[href*='/manga/']")
                if not img:
                    img = box.select_one("img")
                if link and img:
                    break
            if not link:
                continue
            href = link.get("href") or ""
            url = _abs(href)
            slug = _slug_from_url(url or href)
            if slug in seen:
                continue
            seen.add(slug)
            cover = _clean_cover(img.get("data-src") or img.get("src")) if img else None
            out.append(
                MangaInfo(
                    slug=slug,
                    title=title,
                    cover_url=cover,
                    source_slug=slug,
                    source_url=url,
                    provider=self.name,
                )
            )
        return out

    # ------------------------------------------------------------------
    # detail + chapters (HTML)
    # ------------------------------------------------------------------

    def get_manga(self, source_slug: str) -> MangaInfo | None:
        # 1) REST by slug (id + genre/type/status)
        rest_info: MangaInfo | None = None
        try:
            items = self._rest_manga_list({"slug": source_slug, "per_page": 1})
            if items:
                rest_info = items[0]
        except ProviderError:
            pass

        # 2) HTML detail (synopsis, author, cover)
        url = f"{BASE_SITE}/manga/{source_slug}/"
        try:
            html_text = self._get_html(url)
        except ProviderError:
            return rest_info

        soup = BeautifulSoup(html_text, "html.parser")
        h1 = soup.select_one("h1")
        title = _strip_title(h1.get_text(strip=True) if h1 else None)

        synopsis_el = soup.select_one("#Sinopsis > p") or soup.select_one("#Sinopsis")
        synopsis = synopsis_el.get_text("\n", strip=True) if synopsis_el else None

        genres_html: list[str] = []
        for a in soup.select(
            "ul.genre li.genre a span, ul.genre li a, table.inftable a[href*='/genre/']"
        ):
            t = a.get_text(strip=True)
            if t and t not in genres_html and t.lower() not in {"genre", "tema"}:
                genres_html.append(t)

        img = soup.select_one("div.ims img") or soup.select_one(".ims img")
        cover = _clean_cover(img.get("data-src") or img.get("src")) if img else None

        raw_info: dict[str, str] = {}
        for tr in soup.select("table.inftable tr"):
            tds = tr.select("td")
            if len(tds) >= 2:
                raw_info[tds[0].get_text(strip=True)] = tds[1].get_text(strip=True)

        def _pick(*keys: str) -> str | None:
            for k, v in raw_info.items():
                lk = k.lower()
                if any(x in lk for x in keys):
                    return v.strip() or None
            return None

        title = _strip_title(_pick("judul") or title) or source_slug
        alt = _strip_title(_pick("alternatif", "indonesia"))
        status = _parse_status(_pick("status"))
        author = _pick("author", "pengarang", "komikus")
        mtype = _pick("tipe", "type")
        rating = None
        rating_raw = _pick("rating")
        if rating_raw:
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", rating_raw)
            if m:
                rating = float(m.group(1))

        # merge REST + HTML (HTML menang untuk field yang lebih lengkap)
        genres = genres_html or (rest_info.genres if rest_info else [])
        return MangaInfo(
            slug=source_slug,
            title=title,
            title_alt=alt or (rest_info.title_alt if rest_info else None),
            synopsis=synopsis,
            cover_url=cover,
            author=author,
            status=status or (rest_info.status if rest_info else None),
            type=mtype or (rest_info.type if rest_info else None),
            genres=genres,
            rating=rating if rating is not None else (rest_info.rating if rest_info else None),
            source_slug=source_slug,
            source_id=rest_info.source_id if rest_info else None,
            source_url=url,
            provider=self.name,
            raw={
                "info_table": raw_info,
                "rest_id": rest_info.source_id if rest_info else None,
            },
        )

    def get_chapters(self, source_slug: str) -> list[ChapterInfo]:
        slug = (source_slug or "").strip().strip("/")
        if not slug:
            return []
        rows = self.engine.manga_chapters(slug)
        out: list[ChapterInfo] = []
        for r in rows:
            title = r.get("title") or ""
            num = r.get("number")
            if num is None:
                num = _chapter_number(title)
            out.append(
                ChapterInfo(
                    number=num,
                    name=title,
                    url=r.get("url"),
                    source_chapter_id=r.get("url"),
                    published_at=r.get("date"),
                    provider=self.name,
                    raw_name=title,
                )
            )
        return out

    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        """Ambil URL gambar saja (tanpa download binary)."""
        url = chapter.url
        if not url and chapter.source_chapter_id:
            # source_chapter_id kadang slug path
            cid = str(chapter.source_chapter_id)
            if cid.startswith("http"):
                url = cid
            elif "/" in cid:
                url = urljoin(BASE_SITE, cid)
        if not url:
            raise ProviderError(self.name, "chapter url required for pages")
        try:
            images = self.engine.chapter_images(url)
        except Exception:
            # fallback legacy scrape
            images = []
            try:
                html_text = self._get_html(url)
                soup = BeautifulSoup(html_text, "html.parser")
                for img in soup.find_all("img"):
                    src = img.get("data-src") or img.get("src") or ""
                    if src:
                        images.append(src)
            except Exception as e:
                raise ProviderError(self.name, f"pages failed: {e}", e) from e
        images = filter_watermark(images)
        if not images:
            raise ProviderError(self.name, "empty chapter images")
        return ChapterPages.from_urls(
            list(images),
            provider=self.name,
            chapter_number=chapter.number,
            chapter_name=chapter.name,
            source_url=url,
            referer=BASE_SITE + "/",
            ttl_seconds=6 * 3600,
        )

