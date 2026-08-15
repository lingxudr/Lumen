"""
SSRF / open-proxy guards for image + API proxies.

- Allowlist host (exact / suffix)
- Block private / loopback / link-local / metadata IPs
- Block non-http(s) schemes
- Max response size helper
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# Hosts yang boleh di-fetch (suffix match: "cdn.example.com" matches example.com only if listed)
ALLOWED_API_HOSTS = frozenset(
    {
        "api.voratoon.com",
        "v1.voratoon.com",
        "voratoon.com",
        "www.voratoon.com",
        # legacy (DNS sering mati — tetap di list agar tidak break env lama)
        "be.komikcast.cc",
        "komikcast.fit",
        "komikcast.com",
        "komikcast.to",
    }
)

ALLOWED_IMAGE_HOST_SUFFIXES = (
    "imgkc1.my.id",
    "komikcast.fit",
    "komikcast.com",
    "komiku.org", "cvr.voratoon.id", "cdn.voratoon.com", "minio.imgkc1.my.id",
    "thumbnail.komiku.org",
    "img.komiku.org",
    "sankavollerei.web.id",
    "shngm.id",
    "assets.shngm.id",
    "minio.imgkc1.my.id",
)

# substring tokens still used carefully after host parse
ALLOWED_IMAGE_HOST_CONTAINS = (
    "minio.",
    "cdn.",
    "sv1.",
    "sv2.",
    "sv3.",
)

MAX_IMAGE_BYTES = 12 * 1024 * 1024  # 12 MB
MAX_API_BYTES = 4 * 1024 * 1024


def _hostname(url: str) -> str | None:
    try:
        u = urlparse(url)
    except Exception:
        return None
    if u.scheme not in ("http", "https"):
        return None
    host = (u.hostname or "").lower().strip(".")
    if not host:
        return None
    return host


def is_private_host(host: str) -> bool:
    h = (host or "").lower().strip(".")
    if not h:
        return True
    if h in ("localhost", "localhost.localdomain"):
        return True
    # bare IP?
    try:
        ip = ipaddress.ip_address(h)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        pass
    # DNS names that often resolve internal — block obvious
    if h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"):
        return True
    # AWS/GCP metadata hostname
    if h in ("metadata.google.internal", "metadata"):
        return True
    return False


def host_allowed_image(host: str) -> bool:
    h = (host or "").lower().strip(".")
    if not h or is_private_host(h):
        return False
    for suf in ALLOWED_IMAGE_HOST_SUFFIXES:
        if h == suf or h.endswith("." + suf):
            return True
    for token in ALLOWED_IMAGE_HOST_CONTAINS:
        if token in h and not is_private_host(h):
            # still require known manga-related tld-ish
            if any(
                x in h
                for x in (
                    "komik",
                    "imgkc",
                    "shngm",
                    "sanka",
                    "minio",
                )
            ):
                return True
    return False


def host_allowed_api(host: str) -> bool:
    h = (host or "").lower().strip(".")
    if not h or is_private_host(h):
        return False
    return h in ALLOWED_API_HOSTS


def validate_image_url(url: str) -> tuple[bool, str]:
    if not url or not isinstance(url, str):
        return False, "empty"
    url = url.strip()
    if len(url) > 2048:
        return False, "url_too_long"
    if re.search(r"[\x00-\x1f]", url):
        return False, "bad_chars"
    host = _hostname(url)
    if not host:
        return False, "bad_scheme_or_host"
    if is_private_host(host):
        return False, "private_host"
    if not host_allowed_image(host):
        return False, "host_not_allowed"
    return True, "ok"


def validate_api_target(url: str) -> tuple[bool, str]:
    host = _hostname(url)
    if not host:
        return False, "bad_scheme_or_host"
    if is_private_host(host):
        return False, "private_host"
    if not host_allowed_api(host):
        return False, "host_not_allowed"
    return True, "ok"
