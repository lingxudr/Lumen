
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.hybrid_providers.base import (
    classify_exception,
    TimeoutError,
    NetworkError,
    HTTPError,
    ProviderBlocked,
    ParseError,
)


def test_timeout():
    e = classify_exception("kc", Exception("read timed out"))
    assert isinstance(e, TimeoutError)
    assert e.retryable


def test_429():
    e = classify_exception("kc", Exception("HTTP 429 Too Many Requests"))
    assert isinstance(e, HTTPError)
    assert e.kind == "http_429"
    assert e.cooldown_sec >= 60


def test_403():
    e = classify_exception("kc", Exception("403 Forbidden"))
    assert e.kind == "http_403"
    assert e.degrade_provider


def test_blocked():
    e = classify_exception("kc", Exception("Just a moment... Cloudflare"))
    assert isinstance(e, ProviderBlocked)


def test_parse():
    e = classify_exception("kc", Exception("JSON decode error"))
    assert isinstance(e, ParseError)


def test_network():
    e = classify_exception("kc", Exception("Connection refused"))
    assert isinstance(e, NetworkError)


if __name__ == "__main__":
    test_timeout(); test_429(); test_403(); test_blocked(); test_parse(); test_network()
    print("errors ok")
