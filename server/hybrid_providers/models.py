"""Model data internal (hasil normalizer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MangaInfo:
    """Metadata manga yang sudah dinormalisasi."""

    slug: str
    title: str
    title_alt: str | None = None
    synopsis: str | None = None
    cover_url: str | None = None
    author: str | None = None
    status: str | None = None  # Ongoing | Completed | Hiatus | Unknown
    type: str | None = None  # Manga | Manhwa | Manhua
    genres: list[str] = field(default_factory=list)
    rating: float | None = None
    latest_chapter: str | None = None
    latest_chapter_url: str | None = None
    updated_label: str | None = None
    source_slug: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    provider: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "title_alt": self.title_alt,
            "synopsis": self.synopsis,
            "cover_url": self.cover_url,
            "author": self.author,
            "status": self.status,
            "type": self.type,
            "genres": list(self.genres),
            "rating": self.rating,
            "latest_chapter": self.latest_chapter,
            "latest_chapter_url": self.latest_chapter_url,
            "updated_label": self.updated_label,
            "source_slug": self.source_slug,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "provider": self.provider,
        }


@dataclass
class ChapterInfo:
    """Satu chapter dari satu provider."""

    number: float | None
    name: str
    url: str | None = None
    source_chapter_id: str | None = None
    published_at: str | None = None
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name,
            "url": self.url,
            "source_chapter_id": self.source_chapter_id,
            "published_at": self.published_at,
            "provider": self.provider,
        }


@dataclass
class PageInfo:
    """
    Satu halaman reader.

    image_url / source_url BUKAN permanent ID — bisa ber-token & expired.
    Identitas stabil: (provider, provider_page_id | page_index).
    """

    index: int
    image_url: str
    width: int | None = None
    height: int | None = None
    referer: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    provider: str | None = None
    provider_page_id: str | None = None  # id stabil di sumber, bila ada
    source_url: str | None = None  # sama image_url atau origin sebelum proxy
    fetched_at: str | None = None  # ISO UTC
    expires_at: str | None = None  # ISO UTC; None = unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "image_url": self.image_url,
            "width": self.width,
            "height": self.height,
            "referer": self.referer,
            "headers": dict(self.headers or {}),
            "provider": self.provider,
            "provider_page_id": self.provider_page_id,
            "source_url": self.source_url or self.image_url,
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
        }

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except Exception:
            return False
        now = now or _utcnow()
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp


@dataclass
class ChapterPages:
    """
    Hasil playback.

    - pages: urutan wajib dipertahankan (index 0..n-1)
    - images: legacy list[str] untuk kompatibilitas frontend
    URL tidak dianggap permanent; pakai fetched_at / expires_at.
    """

    images: list[str]
    provider: str
    chapter_number: float | None = None
    chapter_name: str | None = None
    source_url: str | None = None
    pages: list[PageInfo] = field(default_factory=list)
    fetched_at: str | None = None
    expires_at: str | None = None  # chapter-level hint
    referer: str | None = None

    def __post_init__(self) -> None:
        # sinkronkan pages ↔ images, jaga urutan
        if self.pages and not self.images:
            self.images = [p.image_url for p in sorted(self.pages, key=lambda x: x.index)]
        elif self.images and not self.pages:
            now = _utcnow().isoformat().replace("+00:00", "Z")
            self.pages = [
                PageInfo(
                    index=i,
                    image_url=u,
                    provider=self.provider,
                    source_url=u,
                    referer=self.referer,
                    fetched_at=self.fetched_at or now,
                    expires_at=self.expires_at,
                )
                for i, u in enumerate(self.images)
                if isinstance(u, str) and u
            ]
            self.images = [p.image_url for p in self.pages]
        elif self.pages:
            ordered = sorted(self.pages, key=lambda x: x.index)
            self.pages = [
                PageInfo(
                    index=i,
                    image_url=p.image_url,
                    width=p.width,
                    height=p.height,
                    referer=p.referer or self.referer,
                    headers=p.headers,
                    provider=p.provider or self.provider,
                    provider_page_id=p.provider_page_id,
                    source_url=p.source_url or p.image_url,
                    fetched_at=p.fetched_at or self.fetched_at,
                    expires_at=p.expires_at or self.expires_at,
                )
                for i, p in enumerate(ordered)
            ]
            self.images = [p.image_url for p in self.pages]

    @staticmethod
    def from_urls(
        urls: list[str],
        *,
        provider: str,
        chapter_number: float | None = None,
        chapter_name: str | None = None,
        source_url: str | None = None,
        referer: str | None = None,
        ttl_seconds: int | None = 6 * 3600,
    ) -> "ChapterPages":
        now = _utcnow()
        fetched = now.isoformat().replace("+00:00", "Z")
        expires = None
        if ttl_seconds and ttl_seconds > 0:
            from datetime import timedelta

            expires = (now + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        clean = [u for u in urls if isinstance(u, str) and u.strip()]
        pages = [
            PageInfo(
                index=i,
                image_url=u.strip(),
                provider=provider,
                source_url=u.strip(),
                referer=referer,
                fetched_at=fetched,
                expires_at=expires,
            )
            for i, u in enumerate(clean)
        ]
        return ChapterPages(
            images=[p.image_url for p in pages],
            provider=provider,
            chapter_number=chapter_number,
            chapter_name=chapter_name,
            source_url=source_url,
            pages=pages,
            fetched_at=fetched,
            expires_at=expires,
            referer=referer,
        )

    def needs_refetch(self) -> bool:
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
                now = _utcnow()
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now >= exp:
                    return True
            except Exception:
                pass
        return any(p.is_expired() for p in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "images": list(self.images),
            "pages": [p.to_dict() for p in self.pages],
            "provider": self.provider,
            "chapter_number": self.chapter_number,
            "chapter_name": self.chapter_name,
            "source_url": self.source_url,
            "page_count": len(self.images),
            "fetched_at": self.fetched_at,
            "expires_at": self.expires_at,
            "referer": self.referer,
        }

    def to_reader_payload(self) -> dict[str, Any]:
        """Shape frontend reader: data.data.images + pages metadata."""
        return {
            "status": 200,
            "message": "ok",
            "data": {
                "data": {
                    "images": list(self.images),
                    "pages": [p.to_dict() for p in self.pages],
                    "index": self.chapter_number,
                    "title": self.chapter_name,
                },
                "chapterIndex": self.chapter_number,
            },
            "meta": {
                "provider": self.provider,
                "fetched_at": self.fetched_at,
                "expires_at": self.expires_at,
                "needs_refetch": self.needs_refetch(),
            },
        }
