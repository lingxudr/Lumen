import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

from security import validate_image_url, validate_api_target


def test_allow_cdn():
    ok, _ = validate_image_url("https://assets.shngm.id/cover.jpg")
    assert ok


def test_block_localhost():
    ok, reason = validate_image_url("http://127.0.0.1/x")
    assert not ok


def test_block_metadata():
    ok, _ = validate_image_url("http://169.254.169.254/latest/meta-data")
    assert not ok


def test_block_file():
    ok, _ = validate_image_url("file:///etc/passwd")
    assert not ok


def test_api_host_pin():
    ok, _ = validate_api_target("https://be.komikcast.cc/series")
    assert ok
    ok2, _ = validate_api_target("https://evil.example/series")
    assert not ok2


if __name__ == "__main__":
    test_allow_cdn()
    test_block_localhost()
    test_block_metadata()
    test_block_file()
    test_api_host_pin()
    print("ssrf ok")
