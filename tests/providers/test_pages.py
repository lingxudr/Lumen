import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.models import ChapterPages, PageInfo


def test_order_preserved():
    urls = [f"https://cdn/img/{i}.jpg" for i in range(5)]
    cp = ChapterPages.from_urls(urls, provider="test")
    assert cp.images == urls
    assert [p.index for p in cp.pages] == [0, 1, 2, 3, 4]


def test_url_not_permanent():
    cp = ChapterPages.from_urls(
        ["https://cdn/x.jpg?token=abc"],
        provider="test",
        ttl_seconds=1,
    )
    assert cp.expires_at
    assert cp.pages[0].source_url
    # identity is index + provider, not URL alone
    assert cp.pages[0].index == 0
    assert cp.pages[0].provider == "test"


def test_reader_payload():
    cp = ChapterPages.from_urls(["https://a/1.jpg", "https://a/2.jpg"], provider="sanka")
    payload = cp.to_reader_payload()
    assert payload["data"]["data"]["images"] == ["https://a/1.jpg", "https://a/2.jpg"]
    assert len(payload["data"]["data"]["pages"]) == 2


if __name__ == "__main__":
    test_order_preserved()
    test_url_not_permanent()
    test_reader_payload()
    print("pages ok")
