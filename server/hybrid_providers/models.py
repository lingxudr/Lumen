"""Model data internal (hasil normalizer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MangaInfo:
    """Metadata manga yang sudah dinormalisasi."""

    slug: str
    title: str
    title_alt: str | None = None
    synopsis: str | None = None
    cover_url: str | None = None
    author: str | None = None
    status: str | None = None          # Ongoing | Completed | Hiatus | Unknown
    type: str | None = None            # Manga | Manhwa | Manhua
    genres: list[str] = field(default_factory=list)
    rating: float | None = None
    source_slug: str | None = None     # slug di provider asal
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
class ChapterPages:
    """Hasil playback: daftar URL gambar."""

    images: list[str]
    provider: str
    chapter_number: float | None = None
    chapter_name: str | None = None
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "images": list(self.images),
            "provider": self.provider,
            "chapter_number": self.chapter_number,
            "chapter_name": self.chapter_name,
            "source_url": self.source_url,
            "page_count": len(self.images),
        }
