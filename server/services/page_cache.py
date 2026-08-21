"""Page URL cache helpers (provider-agnostic dicts)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def pages_from_cache_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    if not doc:
        return None
    pages_raw = doc.get("pages") or []
    images = doc.get("images") or []
    if pages_raw:
        images = [
            p.get("image_url") or p.get("source_url") or ""
            for p in pages_raw
            if (p.get("image_url") or p.get("source_url"))
        ]
    return {
        "provider": doc.get("provider"),
        "images": [u for u in images if u],
        "fetched_at": doc.get("fetched_at"),
        "expires_at": doc.get("expires_at"),
    }


def needs_refetch(doc: dict[str, Any] | None) -> bool:
    if not doc:
        return True
    exp = doc.get("expires_at")
    if not exp:
        return False
    try:
        if isinstance(exp, str):
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        else:
            exp_dt = exp
        return datetime.now(timezone.utc) >= exp_dt
    except Exception:
        return True
