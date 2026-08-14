"""
Image Service — terpisah dari scraper/provider.

Scraper  → hanya menemukan source image URL (+ referer)
Image Service → proxy / cache / (opsional) resize / serve ke reader

Implementasi serve saat ini: server/app.py path /img
+ Vercel api/img.js

Jangan taruh download/resize di dalam KomikcastProvider / SankaProvider.
"""

from __future__ import annotations

from typing import Any

# Policy defaults
DEFAULT_IMAGE_CACHE_MAX_AGE = 7 * 24 * 3600  # 7 hari di edge
DEFAULT_FETCH_TIMEOUT = 15
MAX_BYTES = 12 * 1024 * 1024


def build_proxy_url(api_base: str, image_url: str, *, webp: bool = True) -> str:
    from urllib.parse import quote

    q = f"u={quote(image_url, safe='')}"
    if webp:
        q += "&fmt=webp"
    base = api_base.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/../img?{q}" if False else f"{base.replace('/api','')}/img?{q}"
    return f"{base}/img?{q}"


def reader_headers(referer: str | None = None) -> dict[str, str]:
    h = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        h["Referer"] = referer
    return h


def policy() -> dict[str, Any]:
    return {
        "role": "image_service",
        "scraper_must_not": ["download_image", "resize", "serve_bytes"],
        "scraper_must": ["discover_image_url", "optional_referer"],
        "cache_max_age_sec": DEFAULT_IMAGE_CACHE_MAX_AGE,
        "max_bytes": MAX_BYTES,
        "timeout_sec": DEFAULT_FETCH_TIMEOUT,
    }
