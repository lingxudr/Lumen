"""
Canonical manga matching — lebih kuat dari title_norm saja.

Urutan skor (tinggi → rendah):
  1. exact provider ID / source_id
  2. normalized slug (exact)
  3. normalized title (exact)
  4. alternative title overlap
  5. author + title fuzzy
  6. fuzzy title similarity (threshold default 0.92)
  7. manual alias table

Dipakai Sync Worker untuk mengelompokkan entry multi-provider
ke satu canonical_slug di DB.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable


# Manual alias: alias_norm → canonical_key
# Tambah lewat env/file nanti; seed minimal di sini.
MANUAL_ALIASES: dict[str, str] = {
    "solo leveling": "solo leveling",
    "only i level up": "solo leveling",
    "na honjaman rebereop": "solo leveling",
    "나 혼자만 레벨업": "solo leveling",
    "solo leveling ragnarok": "solo leveling ragnarok",
    "solo leveling 2024": "solo leveling",
}


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).lower().strip()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    for junk in (
        " manhwa",
        " manga",
        " manhua",
        " comic",
        " webtoon",
        " official",
    ):
        if s.endswith(junk):
            s = s[: -len(junk)].strip()
    # buang tahun di akhir: (2024) / 2024
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_slug(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@dataclass
class MatchCandidate:
    """Satu judul dari satu provider, siap di-match."""

    provider: str
    title: str
    slug: str | None = None
    source_id: str | None = None
    title_alt: str | None = None
    author: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def title_norm(self) -> str:
        return normalize_text(self.title)

    @property
    def slug_norm(self) -> str:
        return normalize_slug(self.slug)

    @property
    def alt_norms(self) -> list[str]:
        if not self.title_alt:
            return []
        parts = re.split(r"[,;/|]", str(self.title_alt))
        return [normalize_text(p) for p in parts if normalize_text(p)]

    @property
    def author_norm(self) -> str:
        return normalize_text(self.author)


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def similarity(a: MatchCandidate, b: MatchCandidate) -> tuple[float, str]:
    """
    Return (score 0..1, reason).
    score >= 0.92 → kandidat duplicate kuat.
    """
    # 1) exact provider-local id (hanya berarti bila same provider — skip lintas)
    # lintas provider: source_id jarang sama

    # 2) slug exact
    if a.slug_norm and a.slug_norm == b.slug_norm:
        return 1.0, "slug_exact"

    # 3) title exact
    if a.title_norm and a.title_norm == b.title_norm:
        return 0.99, "title_exact"

    # 7) manual alias → same canonical
    alias_a = MANUAL_ALIASES.get(a.title_norm, a.title_norm)
    alias_b = MANUAL_ALIASES.get(b.title_norm, b.title_norm)
    if alias_a and alias_a == alias_b and alias_a in MANUAL_ALIASES.values():
        # only if at least one was an alias hit
        if a.title_norm in MANUAL_ALIASES or b.title_norm in MANUAL_ALIASES:
            return 0.98, "manual_alias"

    # 4) alt title overlap
    alts_a = set(a.alt_norms + ([a.title_norm] if a.title_norm else []))
    alts_b = set(b.alt_norms + ([b.title_norm] if b.title_norm else []))
    if alts_a & alts_b:
        return 0.96, "alt_title_overlap"

    # 5) author + fuzzy title
    fuzzy_t = _fuzzy(a.title_norm, b.title_norm)
    if a.author_norm and a.author_norm == b.author_norm and fuzzy_t >= 0.85:
        return min(0.97, 0.85 + fuzzy_t * 0.12), "author_title_fuzzy"

    # 6) pure fuzzy title
    if fuzzy_t >= 0.92:
        return fuzzy_t, "title_fuzzy"

    # slug fuzzy (solo-leveling vs solo-leveling-ragnarok must NOT merge)
    if a.slug_norm and b.slug_norm:
        # bila satu slug adalah prefix ketat + suffix beda signifikan → bukan match
        shorter, longer = sorted([a.slug_norm, b.slug_norm], key=len)
        if longer.startswith(shorter + "-") and longer != shorter:
            suffix = longer[len(shorter) + 1 :]
            if suffix not in ("2024", "id", "raw", "official"):
                return 0.0, "slug_suffix_distinct"
        sf = _fuzzy(a.slug_norm, b.slug_norm)
        if sf >= 0.94:
            return sf, "slug_fuzzy"

    return fuzzy_t, "low"


def cluster_candidates(
    items: Iterable[MatchCandidate],
    threshold: float = 0.92,
) -> list[list[MatchCandidate]]:
    """
    Union-find clustering. Tidak menggabungkan bila slug suffix distinct.
    """
    items = list(items)
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            score, reason = similarity(items[i], items[j])
            if score >= threshold and reason != "slug_suffix_distinct":
                union(i, j)

    buckets: dict[int, list[MatchCandidate]] = {}
    for i, it in enumerate(items):
        buckets.setdefault(find(i), []).append(it)
    # prefer larger groups first
    groups = list(buckets.values())
    groups.sort(key=lambda g: (-len(g), g[0].title_norm))
    return groups


def pick_canonical_slug(group: list[MatchCandidate], priority: list[str] | None = None) -> str:
    """Pilih canonical_slug: prioritas provider, lalu slug terpendek stabil."""
    priority = priority or ["komikcast", "shinigami", "komiku", "sanka"]
    by_prov = {c.provider: c for c in group}
    for p in priority:
        c = by_prov.get(p)
        if c and c.slug_norm:
            return c.slug_norm
    for c in group:
        if c.slug_norm:
            return c.slug_norm
    return group[0].title_norm.replace(" ", "-") if group else "unknown"
