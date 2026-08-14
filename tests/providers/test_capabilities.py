import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.base import (
    ALL_CAPABILITIES,
    CAP_LATEST,
    CAP_SEARCH,
    BaseProvider,
)
from server.hybrid_providers.models import ChapterInfo, ChapterPages, MangaInfo
from server.hybrid_providers.manager import ProviderManager


class PartialProvider(BaseProvider):
    name = "partial"
    priority = 50
    capabilities = frozenset({CAP_SEARCH})  # only search

    def search(self, keyword: str, limit: int = 20):
        return [MangaInfo(slug="x", title=keyword or "x")]

    def get_latest(self, limit: int = 20, page: int = 1):
        raise RuntimeError("should not be called")

    def get_manga(self, source_slug: str):
        return None

    def get_chapters(self, source_slug: str):
        return []

    def get_pages(self, chapter: ChapterInfo):
        return ChapterPages(images=[], provider=self.name)


def test_supports():
    p = PartialProvider()
    assert p.supports(CAP_SEARCH)
    assert not p.supports(CAP_LATEST)


def test_manager_skips_unsupported():
    mgr = ProviderManager([PartialProvider()])
    assert mgr.providers_for(CAP_SEARCH)
    assert mgr.providers_for(CAP_LATEST) == []
    # get_latest should not call partial
    assert mgr.get_latest(limit=5) == []


if __name__ == "__main__":
    test_supports()
    test_manager_skips_unsupported()
    print("capabilities ok")
