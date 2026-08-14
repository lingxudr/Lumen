
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.base import BaseProvider, ProviderError, ALL_CAPABILITIES
from server.hybrid_providers.manager import ProviderManager
from server.hybrid_providers.models import (
    CanonicalChapter,
    ChapterInfo,
    ChapterPages,
    ChapterSource,
    MangaInfo,
)


class FakeProvider(BaseProvider):
    capabilities = ALL_CAPABILITIES

    def __init__(self, name: str, priority: int, fail_pages: bool = False, images=None):
        self.name = name
        self.priority = priority
        self.fail_pages = fail_pages
        self.images = images or []
        self.calls = []

    def search(self, keyword: str, limit: int = 20):
        return []

    def get_latest(self, limit: int = 20, page: int = 1):
        return []

    def get_manga(self, source_slug: str):
        return None

    def get_chapters(self, source_slug: str):
        return []

    def get_pages(self, chapter: ChapterInfo) -> ChapterPages:
        self.calls.append(chapter.source_chapter_id)
        if self.fail_pages:
            raise ProviderError(self.name, "pages failed")
        return ChapterPages.from_urls(
            self.images,
            provider=self.name,
            chapter_number=chapter.number,
        )


def test_fallback_uses_each_source_id():
    kc = FakeProvider("komikcast", 10, fail_pages=True)
    ku = FakeProvider("komiku", 20, fail_pages=True)
    sk = FakeProvider("sanka", 30, fail_pages=False, images=["https://cdn/1.jpg"])
    mgr = ProviderManager([kc, ku, sk])

    canon = CanonicalChapter(
        key="100",
        number=100.0,
        name="Chapter 100",
        sources={
            "komikcast": ChapterSource("komikcast", source_chapter_id="abc"),
            "komiku": ChapterSource("komiku", source_chapter_id="xyz"),
            "sanka": ChapterSource("sanka", source_chapter_id="uuid-1"),
        },
    )
    pages = mgr.get_pages(canon)
    assert pages.images == ["https://cdn/1.jpg"]
    assert pages.provider == "sanka"
    assert kc.calls == ["abc"]
    assert ku.calls == ["xyz"]
    assert sk.calls == ["uuid-1"]


def test_number_not_identity():
    c = CanonicalChapter.from_merged_dict(
        {
            "key": "100",
            "number": 100,
            "sources": {
                "komikcast": {"source_chapter_id": "a"},
                "sanka": {"source_chapter_id": "b"},
            },
        }
    )
    assert c.canonical_chapter_id == "100"
    assert c.sources["komikcast"].source_chapter_id == "a"
    assert c.sources["sanka"].source_chapter_id == "b"


if __name__ == "__main__":
    test_fallback_uses_each_source_id()
    test_number_not_identity()
    print("page fallback ok")
