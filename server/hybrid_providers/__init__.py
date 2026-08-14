from .base import BaseProvider, ProviderError
from .manager import ProviderManager
from .models import ChapterInfo, ChapterPages, MangaInfo
from .providers import KomikcastProvider, KomikuProvider
from .sync import SyncJob
from .health import REGISTRY

# Sanka dihapus — IP Railway banned; rantai: Komikcast → Komiku
SankaProvider = None  # type: ignore


def default_manager() -> ProviderManager:
    """Factory: Komikcast (priority) → Komiku (fallback)."""
    return ProviderManager([KomikcastProvider(), KomikuProvider()])


__all__ = [
    "BaseProvider",
    "ProviderError",
    "ProviderManager",
    "MangaInfo",
    "ChapterInfo",
    "ChapterPages",
    "KomikcastProvider",
    "KomikuProvider",
    "SankaProvider",
    "SyncJob",
    "REGISTRY",
    "default_manager",
]
