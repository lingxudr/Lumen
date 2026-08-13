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
from .chapter_dedup import dedupe_provider_chapter_infos
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
        """Ambil latest dari semua provider, interleave agar tidak didominasi satu sumber."""
        buckets: list[list[MangaInfo]] = []
        for p in self.providers:
            try:
                batch = p.get_latest(page=1, limit=limit)
            except ProviderError:
                batch = []
            buckets.append(batch)

        seen: set[str] = set()
        out: list[MangaInfo] = []
        idx = [0] * len(buckets)
        progress = True
        while len(out) < limit and progress:
            progress = False
            for bi, batch in enumerate(buckets):
                while idx[bi] < len(batch):
                    m = batch[idx[bi]]
                    idx[bi] += 1
                    key = (m.title or "").strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.append(m)
                    progress = True
                    break
                if len(out) >= limit:
                    break
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
        by_provider: dict[str, list[ChapterInfo]] = {}
        for p in self.providers:
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            try:
                chapters = p.get_chapters(src_slug)
            except ProviderError:
                continue
            by_provider[p.name] = chapters

        # dedup lintas provider (Chapter 10 / Ch.10 / 10.0 → satu entri)
        deduped = dedupe_provider_chapter_infos(by_provider)

        merged: list[dict[str, Any]] = []
        for entry in deduped:
            sources: dict[str, Any] = {}
            for pname, meta in (entry.get("sources") or {}).items():
                sources[pname] = {
                    "url": meta.get("url"),
                    "source_chapter_id": meta.get("source_chapter_id"),
                    "published_at": meta.get("published_at"),
                    "available": True,
                    "name": meta.get("name"),
                }
            merged.append(
                {
                    "number": entry.get("number"),
                    "name": entry.get("name"),
                    "key": entry.get("key"),
                    "sources": sources,
                    "providers": list(sources.keys()),
                }
            )

        # already sorted desc by dedupe module
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
