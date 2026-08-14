"""
SankaProvider — adapter BaseProvider untuk Sanka Shinigami (+ Komiku-style).

Frontend / route TIDAK memanggil ini langsung → lewat ProviderManager.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseProvider, ProviderError
from ..models import ChapterInfo, ChapterPages, MangaInfo


def _sf():
    try:
        from server.providers import sanka as m
        return m
    except Exception:
        try:
            from providers import sanka as m  # type: ignore
            return m
        except Exception as e:
            raise ProviderError("sanka", f"import failed: {e}") from e


class SankaProvider(BaseProvider):
    name = "sanka"
    priority = 30  # setelah komikcast(10)/komiku(20); naik bila mereka down

    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        try:
            payload = _sf().search(keyword, limit=limit)
        except Exception as e:
            raise ProviderError(self.name, str(e), e) from e
        return [self._from_series_item(it) for it in payload.get("data") or []]

    def get_latest(self, limit: int = 20, page: int = 1) -> list[MangaInfo]:
        try:
            payload = _sf().get_terbaru(limit=limit, prefer="shinigami", page=page)
        except Exception as e:
            raise ProviderError(self.name, str(e), e) from e
        return [self._from_series_item(it) for it in payload.get("data") or []]

    def get_manga(self, source_slug: str) -> MangaInfo | None:
        sf = _sf()
        try:
            if sf.looks_like_uuid(source_slug):
                payload = sf.get_detail_shinigami(source_slug)
                item = payload.get("data") or {}
                # detail dibungkus item series
                if isinstance(item.get("data"), dict):
                    return self._from_series_item(item)
                return self._from_series_item({"data": item, "id": source_slug})
        except Exception as e:
            raise ProviderError(self.name, str(e), e) from e
        return None

    def get_chapters(self, source_slug: str) -> list[ChapterInfo]:
        sf = _sf()
        try:
            if not sf.looks_like_uuid(source_slug):
                return []
            payload = sf.get_chapters_shinigami(source_slug)
        except Exception as e:
            raise ProviderError(self.name, str(e), e) from e
        out: list[ChapterInfo] = []
        for ch in payload.get("data") or []:
            d = ch.get("data") or {}
            num = d.get("index")
            try:
                number = float(num) if num is not None else None
            except (TypeError, ValueError):
                number = None
            out.append(
                ChapterInfo(
                    number=number,
                    name=d.get("title") or f"Chapter {num}",
                    url=None,
                    source_chapter_id=str(ch.get("id") or d.get("chapterId") or ""),
                    published_at=ch.get("createdAt"),
                    provider=self.name,
                )
            )
        return out

    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        sf = _sf()
        try:
            cid = chapter.source_chapter_id
            if cid and sf.looks_like_uuid(str(cid)):
                payload = sf.get_pages_shinigami_by_chapter_id(str(cid))
            else:
                # butuh manga_id di raw — fallback kosong
                raise ProviderError(self.name, "need chapter_id UUID for sanka pages")
            inner = (payload.get("data") or {}).get("data") or payload.get("data") or {}
            images = inner.get("images") or []
            return ChapterPages(
                images=list(images),
                provider=self.name,
                chapter_number=chapter.number,
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(self.name, str(e), e) from e

    @staticmethod
    def _from_series_item(it: dict[str, Any]) -> MangaInfo:
        d = it.get("data") or it
        slug = d.get("slug") or it.get("id") or ""
        latest = d.get("latestChapterLabel") or d.get("latest_chapter")
        if isinstance(latest, dict):
            latest = f"Chapter {latest.get('chapter_number')}"
        return MangaInfo(
            slug=str(slug),
            title=d.get("title") or "",
            title_alt=d.get("nativeTitle") or d.get("alternative_title"),
            synopsis=d.get("synopsis") or d.get("description"),
            cover_url=d.get("coverImage") or d.get("cover"),
            author=d.get("author"),
            status=d.get("status"),
            type=d.get("format") or d.get("type"),
            genres=list(d.get("genres") or []),
            rating=d.get("rating"),
            latest_chapter=str(latest) if latest else None,
            updated_label=d.get("updatedLabel") or d.get("updated_label"),
            source_slug=str(slug),
            source_id=str(d.get("mangaId") or slug),
            provider="sanka",
            raw=it,
        )
