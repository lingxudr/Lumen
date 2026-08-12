"""
Deduplikasi chapter lintas provider.

Aturan kunci (prioritas):
1. Nomor chapter numerik yang dinormalisasi (10, 10.0, "10" → "10")
2. Jika tidak ada nomor → slug dari nama ("chapter-extra-foo")

Merge:
- Satu dokumen per (canonical_slug, key)
- sources: { provider: { url, source_chapter_id, published_at, name } }
- name: pilih yang paling informatif
- number: float kanonik
"""

from __future__ import annotations

import re
from typing import Any


_RE_NUM = re.compile(
    r"(?:chapter|ch\.?|episode|ep\.?|chap\.?|#)\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
_RE_BARE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)$")
_RE_ANY_NUM = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


def parse_chapter_number(name: str | None, number: float | None = None) -> float | None:
    """Ambil nomor chapter terbaik dari field number / nama."""
    if number is not None:
        try:
            return float(number)
        except (TypeError, ValueError):
            pass
    if not name:
        return None
    text = str(name).strip()
    m = _RE_NUM.search(text)
    if m:
        return float(m.group(1))
    m = _RE_BARE.match(text)
    if m:
        return float(m.group(1))
    # last resort: angka pertama (hindari "2" dari "vol 2 ch 15" salah)
    # hanya jika teks pendek
    if len(text) <= 12:
        m = _RE_ANY_NUM.search(text)
        if m:
            return float(m.group(1))
    return None


def normalize_chapter_key(
    number: float | None = None,
    name: str | None = None,
) -> str:
    """
    Kunci dedup stabil.
    10, 10.0, "Chapter 10", "Ch.10" → "10"
    10.5 → "10.5"
    tanpa nomor → "name:<slug>"
    """
    num = parse_chapter_number(name, number)
    if num is not None:
        if float(num).is_integer():
            return str(int(num))
        # trim trailing zeros: 10.50 → 10.5
        s = f"{num:.4f}".rstrip("0").rstrip(".")
        return s

    raw = (name or "").strip().lower()
    raw = re.sub(r"[^\w\s-]", " ", raw)
    raw = re.sub(r"\s+", "-", raw).strip("-")
    return f"name:{raw}" if raw else "name:unknown"


def pick_better_name(a: str | None, b: str | None) -> str | None:
    """Pilih nama yang lebih deskriptif."""
    if not a:
        return b
    if not b:
        return a
    # prefer yang mengandung 'chapter'
    a_ch = bool(re.search(r"chapter|ch\.?", a, re.I))
    b_ch = bool(re.search(r"chapter|ch\.?", b, re.I))
    if a_ch != b_ch:
        return a if a_ch else b
    # prefer lebih panjang (biasanya lebih lengkap)
    return a if len(a) >= len(b) else b


def merge_source_maps(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge sources per provider; field baru menimpa yang kosong saja kecuali url baru."""
    out: dict[str, Any] = dict(old or {})
    for prov, meta in (new or {}).items():
        if not isinstance(meta, dict):
            continue
        prev = dict(out.get(prov) or {})
        for k, v in meta.items():
            if v is None or v == "":
                continue
            # url / id selalu update jika ada nilai baru
            if k in {"url", "source_chapter_id"} and v:
                prev[k] = v
            elif not prev.get(k):
                prev[k] = v
            elif k == "name":
                prev[k] = pick_better_name(prev.get(k), v)
        out[prov] = prev
    return out


def dedupe_chapter_list(
    chapters: list[dict[str, Any]],
    *,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    """
    Dedup list chapter dari satu atau banyak provider.

    Input item boleh:
      {number, name, url, provider, source_chapter_id, published_at}
    atau sudah bentuk:
      {number, name, sources: {prov: {...}}}

    Output sorted desc by number (tanpa number di akhir).
    """
    buckets: dict[str, dict[str, Any]] = {}

    for ch in chapters:
        if not isinstance(ch, dict):
            continue

        # sudah punya sources?
        if isinstance(ch.get("sources"), dict) and ch.get("sources"):
            num = parse_chapter_number(ch.get("name"), ch.get("number"))
            key = normalize_chapter_key(num, ch.get("name"))
            entry = buckets.get(key)
            if entry is None:
                entry = {
                    "key": key,
                    "number": num,
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
                entry["name"] = pick_better_name(entry.get("name"), ch.get("name"))
            entry["sources"] = merge_source_maps(entry.get("sources"), ch.get("sources"))
            continue

        prov = ch.get("provider") or provider or "unknown"
        num = parse_chapter_number(ch.get("name"), ch.get("number"))
        key = normalize_chapter_key(num, ch.get("name"))
        entry = buckets.get(key)
        if entry is None:
            entry = {
                "key": key,
                "number": num,
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
        n = e.get("number")
        has = n is not None
        try:
            nf = float(n) if has else -1.0
        except (TypeError, ValueError):
            has, nf = False, -1.0
        return (has, nf)

    out.sort(key=sort_key, reverse=True)
    return out


def dedupe_provider_chapter_infos(
    by_provider: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """
    by_provider: {"komikcast": [ChapterInfo|dict], "komiku": [...]}
    → list dedup siap simpan DB.
    """
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
