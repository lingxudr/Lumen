
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

from cache_policy import (
    cache_set, cache_get, invalidate_series, invalidate_list, flush_all, tags_for, stats
)

def test_tags():
    assert "series:foo" in tags_for("series/foo/chapters")
    assert "pages:foo:1" in tags_for("series/foo/chapters/1")

def test_cascade():
    flush_all()
    cache_set("k1", b"d", "series/foo")
    cache_set("k2", b"c", "series/foo/chapters")
    cache_set("k3", b"l", "series")
    assert cache_get("k1")
    invalidate_series("foo")
    assert cache_get("k1") is None
    assert cache_get("k2") is None
    assert cache_get("k3") is not None
    invalidate_list()
    assert cache_get("k3") is None

if __name__ == "__main__":
    test_tags()
    test_cascade()
    print("cache_policy ok")
