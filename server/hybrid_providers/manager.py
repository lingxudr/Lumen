"""
ProviderManager — hybrid orchestrator.

- Metadata: coba provider by priority sampai berhasil, merge field kosong.
- Chapters: ambil dari semua provider, merge by number, simpan source per chapter.
- Pages: coba provider yang punya chapter itu by priority sampai dapat gambar.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .base import BaseProvider, ProviderError
from .models import ChapterInfo, ChapterPages, MangaInfo


class ProviderManager:
    def __init__(self, providers: list[BaseProvider] | None = None):
        self.providers = sorted(providers or [], key=lambda p: p.priority)

    def add(self, provider: BaseProvider) -> None:
        self.providers.append(provider)
        self.providers.sort(key=lambda p: p.priority)

    def by_name(self, name: str) -> BaseProvider | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_manga(
        self,
        slug_map: dict[str, str],
        *,
        merge: bool = True,
    ) -> MangaInfo | None:
        """
        slug_map: {"komikcast": "solo-leveling", "komiku": "solo-leveling"}
        Ambil dari provider berurutan; merge field yang kosong jika merge=True.
        """
        result: MangaInfo | None = None
        for p in self.providers:
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            try:
                info = p.get_manga(src_slug)
            except ProviderError:
                continue
            if not info:
                continue
            if result is None:
                result = info
                if not merge:
                    return result
            else:
                result = self._merge_manga(result, info)
        return result

    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        """Gabungan search semua provider, dedupe by title lower."""
        seen: set[str] = set()
        out: list[MangaInfo] = []
        for p in self.providers:
            try:
                batch = p.search(keyword, limit=limit)
            except ProviderError:
                continue
            for m in batch:
                key = (m.title or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(m)
                if len(out) >= limit:
                    return out
        return out

    def get_latest(self, limit: int = 20) -> list[MangaInfo]:
        seen: set[str] = set()
        out: list[MangaInfo] = []
        for p in self.providers:
            try:
                batch = p.get_latest(page=1, limit=limit)
            except ProviderError:
                continue
            for m in batch:
                key = (m.title or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(m)
                if len(out) >= limit:
                    return out
        return out

    # ------------------------------------------------------------------
    # Chapters merge
    # ------------------------------------------------------------------

    def get_chapters_merged(
        self,
        slug_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        """
        Return list:
        [
          {
            "number": 200.0,
            "name": "Chapter 200",
            "sources": {
              "komikcast": {"url": "...", "available": True, ...},
              "komiku": {"url": "...", "available": True, ...},
            }
          },
          ...
        ]
        Terurut number DESC.
        """
        # number -> provider -> ChapterInfo
        bucket: dict[float | str, dict[str, ChapterInfo]] = defaultdict(dict)

        for p in self.providers:
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            try:
                chapters = p.get_chapters(src_slug)
            except ProviderError:
                continue
            for ch in chapters:
                key: float | str
                if ch.number is not None:
                    key = float(ch.number)
                else:
                    key = f"name:{(ch.name or '').lower()}"
                bucket[key][p.name] = ch

        merged: list[dict[str, Any]] = []
        for key, by_prov in bucket.items():
            # pilih name dari provider prioritas tertinggi yang ada
            primary: ChapterInfo | None = None
            for p in self.providers:
                if p.name in by_prov:
                    primary = by_prov[p.name]
                    break
            if primary is None:
                continue
            sources = {}
            for pname, ch in by_prov.items():
                sources[pname] = {
                    "url": ch.url,
                    "source_chapter_id": ch.source_chapter_id,
                    "published_at": ch.published_at,
                    "available": True,
                    "name": ch.name,
                }
            number = key if isinstance(key, float) else primary.number
            merged.append(
                {
                    "number": number,
                    "name": primary.name,
                    "sources": sources,
                }
            )

        def sort_key(item: dict[str, Any]):
            n = item.get("number")
            return (n is not None, n if n is not None else -1)

        merged.sort(key=sort_key, reverse=True)
        return merged

    # ------------------------------------------------------------------
    # Playback pages
    # ------------------------------------------------------------------

    def get_pages(
        self,
        merged_chapter: dict[str, Any],
        preferred: list[str] | None = None,
    ) -> ChapterPages:
        """
        Coba ambil gambar dari provider yang punya chapter ini.
        preferred: urutan nama provider override priority default.
        """
        sources: dict[str, Any] = merged_chapter.get("sources") or {}
        order: list[BaseProvider] = []
        if preferred:
            for name in preferred:
                p = self.by_name(name)
                if p and name in sources:
                    order.append(p)
        for p in self.providers:
            if p not in order and p.name in sources:
                order.append(p)

        errors: list[str] = []
        for p in order:
            meta = sources[p.name]
            ch = ChapterInfo(
                number=merged_chapter.get("number"),
                name=meta.get("name") or merged_chapter.get("name") or "",
                url=meta.get("url"),
                source_chapter_id=meta.get("source_chapter_id"),
                published_at=meta.get("published_at"),
                provider=p.name,
            )
            try:
                pages = p.get_pages(ch)
                if pages.images:
                    return pages
                errors.append(f"{p.name}: empty images")
            except ProviderError as e:
                errors.append(str(e))
            except Exception as e:
                errors.append(f"{p.name}: {e}")

        raise ProviderError(
            "manager",
            "semua provider gagal ambil pages: " + " | ".join(errors),
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _merge_manga(base: MangaInfo, other: MangaInfo) -> MangaInfo:
        """Isi field kosong di base dari other."""

        def pick(a, b):
            return a if a not in (None, "", []) else b

        genres = list(base.genres) or list(other.genres)
        return MangaInfo(
            slug=base.slug or other.slug,
            title=pick(base.title, other.title) or "",
            title_alt=pick(base.title_alt, other.title_alt),
            synopsis=pick(base.synopsis, other.synopsis),
            cover_url=pick(base.cover_url, other.cover_url),
            author=pick(base.author, other.author),
            status=pick(base.status, other.status),
            type=pick(base.type, other.type),
            genres=genres,
            rating=pick(base.rating, other.rating),
            source_slug=base.source_slug,
            source_id=base.source_id,
            source_url=base.source_url,
            provider=base.provider,
            raw={**(other.raw or {}), **(base.raw or {})},
        )
