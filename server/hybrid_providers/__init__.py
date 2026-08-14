from .base import BaseProvider, ProviderError
from .manager import ProviderManager
from .models import ChapterInfo, ChapterPages, MangaInfo
from .providers import KomikcastProvider, KomikuProvider
try:
    from .providers import SankaProvider
except Exception:
    SankaProvider = None  # type: ignore
from .sync import SyncJob
from .health import REGISTRY

def default_manager() -> ProviderManager:
    """Factory: Komikcast → Komiku → Sanka (priority + health)."""
    providers = [KomikcastProvider(), KomikuProvider()]
    if SankaProvider is not None:
        providers.append(SankaProvider())
    return ProviderManager(providers)

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
