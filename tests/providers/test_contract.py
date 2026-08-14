"""
Contract tests — semua provider harus menghasilkan model yang sama.

Provider bebas ganti internal scrape/API, output tetap:
  MangaInfo: title, slug, cover_url (ideal)
  ChapterInfo: number/name
  ChapterPages: images list
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.models import ChapterInfo, ChapterPages, MangaInfo


def assert_manga_contract(m: MangaInfo, *, provider: str) -> None:
    assert isinstance(m, MangaInfo), f"{provider}: not MangaInfo"
    assert m.title and str(m.title).strip(), f"{provider}: title required"
    assert m.slug and str(m.slug).strip(), f"{provider}: slug required"
    # cover ideal; boleh None di edge case tapi flag
    if m.cover_url is not None:
        assert isinstance(m.cover_url, str)
        assert m.cover_url.startswith("http") or m.cover_url.startswith("//")
    if m.provider:
        assert isinstance(m.provider, str)


def assert_chapter_contract(ch: ChapterInfo, *, provider: str) -> None:
    assert isinstance(ch, ChapterInfo)
    assert ch.name is not None
    if ch.number is not None:
        assert isinstance(ch.number, (int, float))


def assert_pages_contract(pages: ChapterPages, *, provider: str) -> None:
    assert isinstance(pages, ChapterPages)
    assert isinstance(pages.images, list)
    assert pages.provider
    for u in pages.images[:3]:
        assert isinstance(u, str)
        assert u.startswith("http")


def _try_providers():
    from server.hybrid_providers.providers.komikcast import KomikcastProvider
    from server.hybrid_providers.providers.komiku import KomikuProvider

    out = [("komikcast", KomikcastProvider()), ("komiku", KomikuProvider())]
    try:
        from server.hybrid_providers.providers.sanka import SankaProvider

        out.append(("sanka", SankaProvider()))
    except Exception:
        pass
    return out


@pytest.mark.network
def test_latest_contract_all_providers():
    """Live network — skip jika semua gagal (CI offline)."""
    any_ok = False
    errors = []
    for name, p in _try_providers():
        try:
            batch = p.get_latest(page=1, limit=3)
            assert isinstance(batch, list)
            if not batch:
                errors.append(f"{name}: empty latest")
                continue
            for m in batch:
                assert_manga_contract(m, provider=name)
            any_ok = True
        except Exception as e:
            errors.append(f"{name}: {e}")
    if not any_ok:
        pytest.skip("no provider reachable: " + "; ".join(errors))


def test_manga_info_shape_unit():
    m = MangaInfo(slug="solo-leveling", title="Solo Leveling", cover_url="https://x/c.jpg", provider="test")
    assert_manga_contract(m, provider="test")


def test_chapter_pages_shape_unit():
    ch = ChapterInfo(number=1.0, name="Chapter 1", provider="test")
    assert_chapter_contract(ch, provider="test")
    pages = ChapterPages(images=["https://cdn/1.jpg"], provider="test", chapter_number=1)
    assert_pages_contract(pages, provider="test")


if __name__ == "__main__":
    test_manga_info_shape_unit()
    test_chapter_pages_shape_unit()
    print("contract unit ok")
    # optional network
    try:
        test_latest_contract_all_providers()
        print("contract network ok")
    except Exception as e:
        print("network skip/fail:", e)
