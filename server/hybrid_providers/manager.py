"""
ProviderManager — single authority untuk semua sumber.

Frontend / route / service TIDAK boleh:
  if komikcast... / if sanka...

Cukup:
  manager.get_series(slug)
  manager.get_chapters(slug_map)
  manager.get_pages(chapter)
  manager.get_latest(limit)
  manager.health_snapshot()

Manager menentukan: priority, health, fallback, timeout/retry (di provider), cache policy di service.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable

from .base import (
    BaseProvider,
    ProviderError,
    classify_exception,
    EmptyResult,
    HTTPError,
    CAP_LATEST,
    CAP_SEARCH,
    CAP_DETAIL,
    CAP_CHAPTERS,
    CAP_PAGES,
)
from .chapter_dedup import dedupe_provider_chapter_infos
from .health import REGISTRY
from .models import ChapterInfo, ChapterPages, MangaInfo


class ProviderManager:
    def __init__(self, providers: list[BaseProvider] | None = None):
        self.providers = list(providers or [])
        self._sort()

    def add(self, provider: BaseProvider) -> None:
        self.providers.append(provider)
        self._sort()

    def by_name(self, name: str) -> BaseProvider | None:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def _sort(self) -> None:
        """Static priority, lalu health (healthy < degraded < down)."""
        status_rank = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}

        def key(p: BaseProvider):
            h = REGISTRY.get(p.name)
            return (status_rank.get(h.status, 1), p.priority, p.name)

        self.providers.sort(key=key)

    def active_providers(self) -> list[BaseProvider]:
        """Skip provider yang circuit-open / down (kecuali semua down → coba semua)."""
        self._sort()
        alive = []
        for p in self.providers:
            h = REGISTRY.get(p.name)
            if h.status == "down":
                continue
            alive.append(p)
        return alive or list(self.providers)

    def providers_for(self, capability: str) -> list[BaseProvider]:
        """Provider aktif yang supports(capability)."""
        out = [p for p in self.active_providers() if p.supports(capability)]
        return out

    def _call(self, provider: BaseProvider, fn: Callable, *args, **kwargs):
        """
        Jalankan call + catat health berdasarkan taksonomi error.

        - timeout/network/5xx → retryable, degrade singkat
        - 429 → cooldown panjang
        - 403/blocked → degrade
        - 404/empty → tidak selalu hukum provider
        - parse → degrade + kind parse
        """
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            ms = (time.time() - t0) * 1000
            REGISTRY.get(provider.name).record_success(ms)
            return result
        except Exception as e:
            ms = (time.time() - t0) * 1000
            err = classify_exception(provider.name, e)
            h = REGISTRY.get(provider.name)
            # EmptyResult / pure 404: jangan naikkan consecutive sekeras down
            if isinstance(err, EmptyResult) or (
                isinstance(err, HTTPError) and err.status == 404
            ):
                h.last_check = time.time()
                h.last_error = str(err)[:240]
                h.last_error_kind = err.kind
                # tidak record_failure penuh
            else:
                h.record_failure(
                    ms,
                    str(err),
                    kind=err.kind,
                    force_cooldown=err.cooldown_sec if err.degrade_provider else None,
                )
            raise err from e

    def _should_try_next(self, err: Exception) -> bool:
        """Apakah manager boleh lanjut ke provider berikutnya."""
        if isinstance(err, ProviderError):
            if err.skip_other_providers:
                return False
            # 404 pada detail: tetap boleh coba provider lain (slug beda)
            return True
        return True

    def health_snapshot(self) -> list[dict[str, Any]]:
        for p in self.providers:
            REGISTRY.get(p.name)
        rows = REGISTRY.snapshot()
        by_name = {p.name: p for p in self.providers}
        for r in rows:
            p = by_name.get(r.get("provider") or "")
            if p is not None:
                r["capabilities"] = p.capability_map()
        return rows

    # ---- Public API (single authority) ----

    def get_series(self, slug_map: dict[str, str], *, merge: bool = True) -> MangaInfo | None:
        """Alias get_manga — single entry detail."""
        return self.get_manga(slug_map, merge=merge)

    def get_chapters(self, slug_map: dict[str, str]) -> list[dict[str, Any]]:
        """Alias get_chapters_merged."""
        return self.get_chapters_merged(slug_map)

    def probe_all(self) -> list[dict[str, Any]]:
        """Health check aktif (1 latest item) per provider."""
        rows = []
        for p in self.providers:
            t0 = time.time()
            try:
                batch = p.get_latest(page=1, limit=1)
                ms = (time.time() - t0) * 1000
                REGISTRY.get(p.name).record_success(ms)
                rows.append({"provider": p.name, "ok": True, "latency_ms": round(ms, 1), "sample": len(batch)})
            except Exception as e:
                ms = (time.time() - t0) * 1000
                REGISTRY.get(p.name).record_failure(ms, str(e))
                rows.append({"provider": p.name, "ok": False, "latency_ms": round(ms, 1), "error": str(e)[:200]})
        return rows

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
        for p in self.providers_for(CAP_DETAIL):
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            try:
                info = self._call(p, p.get_manga, src_slug)
            except ProviderError as e:
                if not self._should_try_next(e):
                    break
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
        for p in self.providers_for(CAP_SEARCH):
            try:
                batch = self._call(p, p.search, keyword, limit=limit)
            except Exception:
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

    def get_latest(self, limit: int = 20, page: int = 1) -> list[MangaInfo]:
        """Ambil latest dari provider aktif (health-aware), interleave."""
        buckets: list[list[MangaInfo]] = []
        for p in self.providers_for(CAP_LATEST):
            try:
                batch = self._call(p, p.get_latest, page=page, limit=limit)
            except Exception:
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
