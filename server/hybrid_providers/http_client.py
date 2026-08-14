"""
HTTP client untuk scraper (JSON/HTML saja).

- Exponential backoff + jitter
- 404: jangan retry
- 403: jangan spam
- 429: hormati Retry-After / cooldown
- TIDAK download/resize/cache image — itu tugas Image Service
"""

from __future__ import annotations

import random
import time
from typing import Any

import requests

from .base import (
    HTTPError,
    NetworkError,
    ParseError,
    ProviderBlocked,
    TimeoutError,
)


DEFAULT_TIMEOUT = 10
MAX_ATTEMPTS = 3


def _sleep_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> None:
    # attempt 0 → ~0.5s, 1 → ~1.5s, 2 → ~3.5s + jitter
    delay = min(cap, base * (3 ** attempt))
    delay *= 0.5 + random.random()  # jitter 50–150%
    time.sleep(delay)


def _raise_http(provider: str, status: int, body_snip: str = "") -> None:
    msg = f"HTTP {status}"
    if body_snip:
        msg += f": {body_snip[:120]}"
    if status == 403:
        low = body_snip.lower()
        if any(x in low for x in ("cloudflare", "just a moment", "cf-ray", "captcha")):
            raise ProviderBlocked(provider, msg, status=status)
    raise HTTPError(provider, msg, status)


def request_json(
    provider: str,
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> Any:
    """GET/POST JSON dengan retry cerdas."""
    sess = session or requests.Session()
    last_err: Exception | None = None

    for attempt in range(max_attempts):
        try:
            r = sess.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                allow_redirects=True,
            )
        except requests.Timeout as e:
            last_err = TimeoutError(provider, str(e), e)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err from e
        except requests.RequestException as e:
            last_err = NetworkError(provider, str(e), e)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err from e

        status = r.status_code
        text = (r.text or "")[:400]

        if status == 404:
            _raise_http(provider, 404, text)  # no retry

        if status == 403:
            _raise_http(provider, 403, text)  # no spam retry

        if status == 429:
            # cooldown: Retry-After
            ra = r.headers.get("Retry-After")
            wait = 0.0
            if ra:
                try:
                    wait = float(ra)
                except ValueError:
                    wait = 5.0
            else:
                wait = min(30.0, 3.0 * (attempt + 1))
            if attempt + 1 < max_attempts:
                time.sleep(wait + random.random())
                last_err = HTTPError(provider, f"HTTP 429 Retry-After={ra}", 429)
                continue
            raise HTTPError(provider, f"HTTP 429 Retry-After={ra}", 429)

        if status >= 500:
            last_err = HTTPError(provider, f"HTTP {status}", status)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err

        if status >= 400:
            _raise_http(provider, status, text)

        # blocked HTML challenge disguised as 200
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" in ctype and "json" not in ctype:
            low = text.lower()
            if any(x in low for x in ("cloudflare", "just a moment", "cf-ray")):
                raise ProviderBlocked(provider, "HTML challenge / Cloudflare", status=status)

        try:
            return r.json()
        except Exception as e:
            raise ParseError(provider, f"invalid JSON: {e}", e) from e

    if last_err:
        raise last_err
    raise NetworkError(provider, "request failed")


def request_text(
    provider: str,
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> str:
    """GET text/HTML — tetap tanpa image binary."""
    sess = session or requests.Session()
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            r = sess.get(url, headers=headers, timeout=timeout)
        except requests.Timeout as e:
            last_err = TimeoutError(provider, str(e), e)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err from e
        except requests.RequestException as e:
            last_err = NetworkError(provider, str(e), e)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err from e

        if r.status_code == 404:
            raise HTTPError(provider, "HTTP 404", 404)
        if r.status_code == 403:
            raise HTTPError(provider, "HTTP 403", 403)
        if r.status_code == 429:
            if attempt + 1 < max_attempts:
                ra = r.headers.get("Retry-After")
                try:
                    time.sleep(float(ra) if ra else 3.0)
                except ValueError:
                    time.sleep(3.0)
                continue
            raise HTTPError(provider, "HTTP 429", 429)
        if r.status_code >= 500:
            last_err = HTTPError(provider, f"HTTP {r.status_code}", r.status_code)
            if attempt + 1 < max_attempts:
                _sleep_backoff(attempt)
                continue
            raise last_err
        if r.status_code >= 400:
            raise HTTPError(provider, f"HTTP {r.status_code}", r.status_code)
        return r.text or ""
    if last_err:
        raise last_err
    raise NetworkError(provider, "request failed")
