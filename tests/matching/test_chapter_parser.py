import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.chapter_dedup import (
    parse_chapter_rich,
    parse_chapter_number,
    normalize_chapter_key,
    sort_chapters_asc,
    dedupe_chapter_list,
)


def test_formats():
    cases = [
        ("Chapter 12", 12.0),
        ("Ch. 12", 12.0),
        ("12", 12.0),
        ("12.5", 12.5),
        ("12-1", 12.1),
        ("Chapter 12 Part 2", 12.0),
        ("Ep 12", 12.0),
        ("12화", 12.0),
        ("Vol. 3 Ch. 12", 12.0),
    ]
    for raw, expect in cases:
        p = parse_chapter_rich(raw)
        assert p.number == expect, (raw, p.number, expect)


def test_volume_part():
    p = parse_chapter_rich("Vol. 3 Ch. 12")
    assert p.volume == 3
    assert p.number == 12.0
    p2 = parse_chapter_rich("Chapter 12 Part 2")
    assert p2.part == 2
    assert p2.number == 12.0


def test_sort_not_string():
    rows = [
        {"name": "Chapter 10", "number": 10},
        {"name": "Chapter 2", "number": 2},
        {"name": "Chapter 11.5", "number": 11.5},
        {"name": "Chapter 12", "number": 12},
        {"name": "Chapter 1", "number": 1},
        {"name": "Chapter 12.1", "number": 12.1},
    ]
    asc = sort_chapters_asc(rows)
    nums = [parse_chapter_number(r["name"], r["number"]) for r in asc]
    assert nums == [1, 2, 10, 11.5, 12, 12.1]


def test_dedupe_sort_desc():
    rows = [
        {"name": "Ch. 1", "number": 1, "provider": "a"},
        {"name": "Chapter 10", "number": 10, "provider": "a"},
        {"name": "Ch. 2", "number": 2, "provider": "b"},
    ]
    out = dedupe_chapter_list(rows)
    nums = [e["number"] for e in out]
    assert nums == [10, 2, 1]


def test_key_stable():
    assert normalize_chapter_key(10, "Chapter 10") == "10"
    assert normalize_chapter_key(10.5, "Ch. 10.5") == "10.5"


if __name__ == "__main__":
    test_formats()
    test_volume_part()
    test_sort_not_string()
    test_dedupe_sort_desc()
    test_key_stable()
    print("chapter parser ok")
