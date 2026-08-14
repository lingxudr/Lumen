import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.services.canonical_match import (
    MatchCandidate,
    cluster_candidates,
    decide,
    similarity,
)


def test_ragnarok_separate():
    a = MatchCandidate("kc", "Solo Leveling", "solo-leveling")
    b = MatchCandidate("ku", "Solo Leveling Ragnarok", "solo-leveling-ragnarok")
    score, reason = similarity(a, b)
    assert decide(score, reason) == "separate"


def test_episode_a_candidate_or_separate():
    a = MatchCandidate("kc", "One Piece", "one-piece")
    b = MatchCandidate("ku", "One Piece: Episode A", "one-piece-episode-a")
    score, reason = similarity(a, b)
    # suffix distinct or not high enough for auto
    assert decide(score, reason) in ("separate", "candidate")
    assert decide(score, reason) != "auto_merge" or score >= 0.97


def test_exact_auto():
    a = MatchCandidate("kc", "Solo Leveling", "solo-leveling")
    b = MatchCandidate("ku", "solo leveling", "solo-leveling")
    score, reason = similarity(a, b)
    assert decide(score, reason) == "auto_merge"


def test_cluster_no_auto_ragnarok():
    items = [
        MatchCandidate("kc", "Solo Leveling", "solo-leveling"),
        MatchCandidate("ku", "Solo Leveling Ragnarok", "solo-leveling-ragnarok"),
    ]
    groups, cands = cluster_candidates(items)
    assert len(groups) == 2


if __name__ == "__main__":
    test_ragnarok_separate()
    test_episode_a_candidate_or_separate()
    test_exact_auto()
    test_cluster_no_auto_ragnarok()
    print("match tiers ok")
