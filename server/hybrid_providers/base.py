"""
BaseProvider — kontrak semua provider (komikcast, komiku, ...).

Frontend / API internal TIDAK memanggil provider langsung.
Semua lewat ProviderManager + Normalizer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import ChapterInfo, ChapterPages, MangaInfo


class ProviderError(Exception):
    """Error generik dari provider."""

    def __init__(self, provider: str, message: str, cause: Exception | None = None):
        self.provider = provider
        self.cause = cause
        super().__init__(f"[{provider}] {message}")


class BaseProvider(ABC):
    """Interface wajib diimplementasi setiap adapter."""

    name: str = "base"
    priority: int = 100  # semakin kecil semakin diprioritaskan

    @abstractmethod
    def search(self, keyword: str, limit: int = 20) -> list[MangaInfo]:
        """Cari manga by keyword."""

    @abstractmethod
    def get_latest(self, page: int = 1, limit: int = 20) -> list[MangaInfo]:
        """Daftar update / newest."""

    @abstractmethod
    def get_manga(self, source_slug: str) -> MangaInfo | None:
        """Detail metadata by slug provider."""

    @abstractmethod
    def get_chapters(self, source_slug: str) -> list[ChapterInfo]:
        """
        Daftar chapter, idealnya terurut number DESC (terbaru dulu).
        number boleh None jika tidak ter-parse; merger akan handle.
        """

    @abstractmethod
    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        """
        Ambil URL gambar chapter untuk playback.
        Harus sudah filter watermark/iklan sebisa mungkin.
        """

    def health(self) -> dict[str, Any]:
        """Cek sederhana apakah provider hidup."""
        try:
            items = self.get_latest(page=1, limit=1)
            return {"provider": self.name, "ok": True, "sample": len(items)}
        except Exception as e:
            return {"provider": self.name, "ok": False, "error": str(e)}
