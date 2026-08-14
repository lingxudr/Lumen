"""
Canonical manga matching — confidence tiers (bukan auto-merge 0.92 mutlak).

>= 0.97          AUTO MERGE
0.92 – 0.97      MATCH CANDIDATE (butuh review / evidence ekstra)
< 0.92           SEPARATE

Evidence ekstra: author, year, alternative_title, type, country.
Proteksi: Solo Leveling ≠ Solo Leveling Ragnarok (slug suffix).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Literal

MatchDecision = Literal["auto_merge", "candidate", "separate"]

AUTO_MERGE_THRESHOLD = 0.97
CANDIDATE_THRESHOLD = 0.92

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
    provider: str
    title: str
    slug: str | None = None
    source_id: str | None = None
    title_alt: str | None = None
    author: str | None = None
    year: int | str | None = None
    type: str | None = None  # Manga / Manhwa / Manhua
    country: str | None = None  # KR / JP / CN / ...
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

    @property
    def year_norm(self) -> str:
        if self.year is None:
            return ""
        m = re.search(r"(19|20)\d{2}", str(self.year))
        return m.group(0) if m else str(self.year).strip()

    @property
    def type_norm(self) -> str:
        return normalize_text(self.type)

    @property
    def country_norm(self) -> str:
        return (self.country or "").strip().upper()


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _slug_suffix_distinct(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return False
    shorter, longer = sorted([a, b], key=len)
    if longer.startswith(shorter + "-"):
        suffix = longer[len(shorter) + 1 :]
        if suffix not in ("2024", "id", "raw", "official", "indo", "indonesia"):
            return True
    return False


def similarity(a: MatchCandidate, b: MatchCandidate) -> tuple[float, str]:
    """Return (score 0..1, reason). Tidak memutus merge — pakai decide()."""
    if a.slug_norm and b.slug_norm and _slug_suffix_distinct(a.slug_norm, b.slug_norm):
        return 0.0, "slug_suffix_distinct"

    if a.slug_norm and a.slug_norm == b.slug_norm:
        return 1.0, "slug_exact"

    if a.title_norm and a.title_norm == b.title_norm:
        return 0.99, "title_exact"

    alias_a = MANUAL_ALIASES.get(a.title_norm, a.title_norm)
    alias_b = MANUAL_ALIASES.get(b.title_norm, b.title_norm)
    if (
        alias_a
        and alias_a == alias_b
        and (a.title_norm in MANUAL_ALIASES or b.title_norm in MANUAL_ALIASES)
    ):
        return 0.985, "manual_alias"

    alts_a = set(a.alt_norms + ([a.title_norm] if a.title_norm else []))
    alts_b = set(b.alt_norms + ([b.title_norm] if b.title_norm else []))
    if alts_a & alts_b:
        return 0.96, "alt_title_overlap"

    fuzzy_t = _fuzzy(a.title_norm, b.title_norm)

    # evidence boosts (capped)
    boost = 0.0
    reasons = [f"fuzzy={fuzzy_t:.3f}"]
    if a.author_norm and a.author_norm == b.author_norm:
        boost += 0.04
        reasons.append("author")
    if a.year_norm and a.year_norm == b.year_norm:
        boost += 0.02
        reasons.append("year")
    if a.type_norm and a.type_norm == b.type_norm:
        boost += 0.01
        reasons.append("type")
    if a.country_norm and a.country_norm == b.country_norm:
        boost += 0.01
        reasons.append("country")

    # author + strong fuzzy
    if a.author_norm and a.author_norm == b.author_norm and fuzzy_t >= 0.85:
        score = min(0.99, 0.88 + fuzzy_t * 0.1 + boost)
        return score, "author_title+" + "+".join(reasons)

    if a.slug_norm and b.slug_norm:
        sf = _fuzzy(a.slug_norm, b.slug_norm)
        if sf >= 0.94:
            return min(0.99, sf + boost * 0.5), "slug_fuzzy"

    score = min(0.99, fuzzy_t + boost)
    return score, "+".join(reasons)


def decide(score: float, reason: str = "") -> MatchDecision:
    if reason == "slug_suffix_distinct" or score < CANDIDATE_THRESHOLD:
        return "separate"
    if score >= AUTO_MERGE_THRESHOLD:
        return "auto_merge"
    return "candidate"


def cluster_candidates(
    items: Iterable[MatchCandidate],
    *,
    auto_threshold: float = AUTO_MERGE_THRESHOLD,
    candidate_threshold: float = CANDIDATE_THRESHOLD,
) -> tuple[list[list[MatchCandidate]], list[dict[str, Any]]]:
    """
    Union-find hanya untuk AUTO MERGE (>= auto_threshold).
    Kandidat 0.92–0.97 dikembalikan terpisah (tidak digabung otomatis).
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

    candidates: list[dict[str, Any]] = []

    for i in range(n):
        for j in range(i + 1, n):
            score, reason = similarity(items[i], items[j])
            decision = decide(score, reason)
            if decision == "auto_merge":
                union(i, j)
            elif decision == "candidate":
                candidates.append(
                    {
                        "a": items[i].title,
                        "b": items[j].title,
                        "provider_a": items[i].provider,
                        "provider_b": items[j].provider,
                        "score": round(score, 4),
                        "reason": reason,
                        "decision": "candidate",
                    }
                )

    buckets: dict[int, list[MatchCandidate]] = {}
    for i, it in enumerate(items):
        buckets.setdefault(find(i), []).append(it)
    groups = list(buckets.values())
    groups.sort(key=lambda g: (-len(g), g[0].title_norm))
    return groups, candidates


def pick_canonical_slug(
    group: list[MatchCandidate], priority: list[str] | None = None
) -> str:
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
