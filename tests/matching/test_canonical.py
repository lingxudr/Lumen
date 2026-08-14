import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.services.canonical_match import MatchCandidate, cluster_candidates, similarity


def test_exact_slug():
    a = MatchCandidate("komikcast", "Solo Leveling", "solo-leveling")
    b = MatchCandidate("komiku", "solo leveling", "solo-leveling")
    score, reason = similarity(a, b)
    assert score >= 0.92
    assert reason in ("slug_exact", "title_exact")


def test_ragnarok_not_merged():
    a = MatchCandidate("komikcast", "Solo Leveling", "solo-leveling")
    c = MatchCandidate("sanka", "Solo Leveling Ragnarok", "solo-leveling-ragnarok")
    score, reason = similarity(a, c)
    assert reason == "slug_suffix_distinct" or score < 0.92


def test_alias_korean():
    a = MatchCandidate("komikcast", "Solo Leveling", "solo-leveling")
    d = MatchCandidate("sanka", "나 혼자만 레벨업", "na-honjaman")
    score, reason = similarity(a, d)
    assert score >= 0.92


def test_cluster():
    items = [
        MatchCandidate("komikcast", "Solo Leveling", "solo-leveling"),
        MatchCandidate("komiku", "solo leveling", "solo-leveling"),
        MatchCandidate("sanka", "Solo Leveling Ragnarok", "solo-leveling-ragnarok"),
    ]
    groups, _cands = cluster_candidates(items)
    assert len(groups) == 2


if __name__ == "__main__":
    test_exact_slug()
    test_ragnarok_not_merged()
    test_alias_korean()
    test_cluster()
    print("canonical ok")
