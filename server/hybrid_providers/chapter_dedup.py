"""
Deduplikasi chapter + parser nomor yang kuat + sorting kanonik.

Parser menangani:
  Chapter 12 | Ch. 12 | 12 | 12.5 | 12-1 | Chapter 12 Part 2
  Ep 12 | 12화 | Vol. 3 Ch. 12

Struktur parse:
  { number, volume, part, fraction, raw_title, sort_key }

Sorting numerik (bukan string):
  11, 11.5, 12, 12.1, 12.2, 13
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParsedChapter:
    number: float | None  # main chapter number (12, 12.5)
    volume: int | None = None
    part: int | None = None  # Part 2 / 12-1 second component as part when intentional
    fraction: float | None = None  # .5 / .1 from 12.5
    raw_title: str = ""
    sort_tuple: tuple = ()  # for stable numeric sort

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "volume": self.volume,
            "part": self.part,
            "fraction": self.fraction,
            "raw_title": self.raw_title,
            "sort_key": list(self.sort_tuple),
        }


# Patterns (order matters)
_RE_VOL_CH = re.compile(
    r"(?:vol(?:ume)?\.?\s*(\d+)\s*[,:\-]?\s*)?"
    r"(?:chapter|ch\.?|episode|ep\.?|chap\.?|#|화)\s*"
    r"(\d+)(?:[.\-](\d+))?"
    r"(?:\s*(?:part|pt\.?)\s*(\d+))?",
    re.I,
)
_RE_CH = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?|chap\.?|#)\s*(\d+)(?:[.\-](\d+))?"
    r"(?:\s*(?:part|pt\.?)\s*(\d+))?",
    re.I,
)
_RE_HANGUL = re.compile(r"(\d+)\s*화")
_RE_BARE = re.compile(
    r"^(\d+)(?:[.\-](\d+))?(?:\s*(?:part|pt\.?)\s*(\d+))?$",
    re.I,
)
_RE_PART_ONLY = re.compile(r"(?:part|pt\.?)\s*(\d+)", re.I)
_RE_VOL_ONLY = re.compile(r"(?:vol(?:ume)?\.?)\s*(\d+)", re.I)


def parse_chapter_rich(name: str | None, number: float | None = None) -> ParsedChapter:
    """
    Parse judul chapter → struktur kaya.
    number field (dari API) dipakai sebagai hint utama bila ada.
    """
    raw = (name or "").strip()
    volume = None
    part = None
    main: float | None = None
    frac: float | None = None

    # 1) explicit numeric from API
    if number is not None:
        try:
            main = float(number)
        except (TypeError, ValueError):
            main = None

    text = raw
    if text:
        m = _RE_VOL_CH.search(text)
        if not m:
            m = _RE_CH.search(text)
        if m:
            # groups vary: vol_ch has vol, main, sub, part
            groups = m.groups()
            if len(groups) == 4:
                vol_s, maj_s, sub_s, part_s = groups
            else:
                vol_s = None
                maj_s, sub_s, part_s = (groups + (None, None, None))[:3]
            if vol_s:
                volume = int(vol_s)
            if maj_s and main is None:
                if sub_s:
                    main = float(f"{int(maj_s)}.{int(sub_s)}")
                    frac = float(f"0.{int(sub_s)}")
                else:
                    main = float(int(maj_s))
            elif maj_s and main is not None and sub_s and float(main).is_integer():
                # enrich with sub from title
                main = float(f"{int(main)}.{int(sub_s)}")
                frac = float(f"0.{int(sub_s)}")
            if part_s:
                part = int(part_s)
        else:
            mh = _RE_HANGUL.search(text)
            if mh and main is None:
                main = float(int(mh.group(1)))
            else:
                mb = _RE_BARE.match(text)
                if mb and main is None:
                    maj_s, sub_s, part_s = mb.group(1), mb.group(2), mb.group(3)
                    if sub_s:
                        main = float(f"{int(maj_s)}.{int(sub_s)}")
                        frac = float(f"0.{int(sub_s)}")
                    else:
                        main = float(int(maj_s))
                    if part_s:
                        part = int(part_s)

        if volume is None:
            mv = _RE_VOL_ONLY.search(text)
            if mv:
                volume = int(mv.group(1))
        if part is None:
            mp = _RE_PART_ONLY.search(text)
            if mp:
                part = int(mp.group(1))

    # fraction from main
    if main is not None and frac is None and not float(main).is_integer():
        frac = main - int(main)

    # sort: volume, main number, part, raw
    sort_tuple = (
        volume if volume is not None else -1,
        main if main is not None else float("-inf"),
        part if part is not None else -1,
        raw.lower(),
    )
    return ParsedChapter(
        number=main,
        volume=volume,
        part=part,
        fraction=frac,
        raw_title=raw,
        sort_tuple=sort_tuple,
    )


def parse_chapter_number(name: str | None, number: float | None = None) -> float | None:
    """Backward-compatible: hanya angka utama."""
    return parse_chapter_rich(name, number).number


def chapter_sort_key(
    number: float | None = None,
    name: str | None = None,
    *,
    reverse_ready: bool = False,
) -> tuple:
    """
    Kunci sort numerik kanonik.
    Asc: 11, 11.5, 12, 12.1, 12.2, 13
    """
    p = parse_chapter_rich(name, number)
    return p.sort_tuple


def normalize_chapter_key(
    number: float | None = None,
    name: str | None = None,
) -> str:
    """
    Kunci dedup stabil.
    10, 10.0, "Chapter 10", "Ch.10" → "10"
    10.5 → "10.5"
    Part tidak mengubah key nomor utama kecuali 12-1 → "12.1"
    """
    p = parse_chapter_rich(name, number)
    if p.number is not None:
        num = p.number
        if float(num).is_integer():
            base = str(int(num))
        else:
            base = f"{num:.4f}".rstrip("0").rstrip(".")
        # part sebagai suffix dedup terpisah hanya jika tidak sudah di number
        if p.part is not None and float(num).is_integer():
            return f"{base}-p{p.part}"
        return base

    raw = (name or "").strip().lower()
    raw = re.sub(r"[^\w\s-]", " ", raw)
    raw = re.sub(r"\s+", "-", raw).strip("-")
    return f"name:{raw}" if raw else "name:unknown"


def pick_better_name(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    a_ch = bool(re.search(r"chapter|ch\.?", a, re.I))
    b_ch = bool(re.search(r"chapter|ch\.?", b, re.I))
    if a_ch != b_ch:
        return a if a_ch else b
    return a if len(a) >= len(b) else b


def merge_source_maps(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = dict(a or {})
    for k, v in (b or {}).items():
        if k not in out or not out[k]:
            out[k] = v
        elif isinstance(v, dict) and isinstance(out[k], dict):
            merged = dict(out[k])
            for sk, sv in v.items():
                if sv and not merged.get(sk):
                    merged[sk] = sv
            out[k] = merged
        elif v and not out[k]:
            out[k] = v
    return out


def dedupe_chapter_list(
    chapters: list[dict[str, Any]],
    *,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    """
    Dedup list dict chapter → satu entry per key, sort DESC numerik.
    """
    buckets: dict[str, dict[str, Any]] = {}

    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        # already-merged shape
        if "key" in ch and "sources" in ch and ch.get("number") is not None:
            key = ch["key"]
            entry = buckets.get(key)
            if entry is None:
                buckets[key] = dict(ch)
            else:
                num = ch.get("number")
                if num is not None and (
                    entry.get("number") is None
                    or float(num) > float(entry["number"] or 0)
                ):
                    entry["number"] = num
                entry["name"] = pick_better_name(entry.get("name"), ch.get("name"))
                entry["sources"] = merge_source_maps(entry.get("sources"), ch.get("sources"))
            continue

        prov = ch.get("provider") or provider or "unknown"
        parsed = parse_chapter_rich(ch.get("name"), ch.get("number"))
        num = parsed.number
        key = normalize_chapter_key(num, ch.get("name"))
        entry = buckets.get(key)
        if entry is None:
            entry = {
                "key": key,
                "number": num,
                "volume": parsed.volume,
                "part": parsed.part,
                "name": ch.get("name"),
                "sources": {},
            }
            buckets[key] = entry
        else:
            if num is not None and (
                entry.get("number") is None
                or float(num) > float(entry["number"] or 0)
            ):
                entry["number"] = num
            if parsed.volume is not None and entry.get("volume") is None:
                entry["volume"] = parsed.volume
            if parsed.part is not None and entry.get("part") is None:
                entry["part"] = parsed.part
            entry["name"] = pick_better_name(entry.get("name"), ch.get("name"))

        src_meta = {
            "url": ch.get("url"),
            "source_chapter_id": ch.get("source_chapter_id"),
            "published_at": ch.get("published_at"),
            "name": ch.get("name"),
        }
        entry["sources"] = merge_source_maps(
            entry.get("sources"), {prov: src_meta}
        )

    out = list(buckets.values())
    for e in out:
        e["providers"] = list((e.get("sources") or {}).keys())

    def sort_key(e: dict[str, Any]):
        p = parse_chapter_rich(e.get("name"), e.get("number"))
        # DESC: negate volume/main for reverse numeric
        vol = p.volume if p.volume is not None else -1
        main = p.number if p.number is not None else float("-inf")
        part = p.part if p.part is not None else -1
        return (vol, main, part)

    out.sort(key=sort_key, reverse=True)
    return out


def sort_chapters_asc(
    chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort ascending: 11, 11.5, 12, 12.1, 12.2, 13."""

    def sk(e: dict[str, Any]):
        return chapter_sort_key(e.get("number"), e.get("name"))

    return sorted(chapters, key=sk)


def dedupe_provider_chapter_infos(
    by_provider: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for prov, items in by_provider.items():
        for ch in items:
            if hasattr(ch, "to_dict"):
                d = ch.to_dict()
            elif isinstance(ch, dict):
                d = dict(ch)
            else:
                continue
            d["provider"] = d.get("provider") or prov
            flat.append(d)
    return dedupe_chapter_list(flat)
