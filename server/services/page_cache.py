"""
Page URL cache policy.

Image URLs are ephemeral (tokens expire). Do not use URL as permanent ID.
Cache entry:
  provider, provider_chapter_id, page_index, source_url, fetched_at, expires_at
On expired → re-fetch via ProviderManager.get_pages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from server.hybrid_providers.models import ChapterPages, PageInfo


def pages_from_cache_doc(doc: dict[str, Any]) -> ChapterPages | None:
    if not doc:
        return None
    pages_raw = doc.get("pages") or []
    images = doc.get("images") or []
    if pages_raw:
        pages = [
            PageInfo(
                index=int(p.get("index", i)),
                image_url=p.get("image_url") or p.get("source_url") or "",
                width=p.get("width"),
                height=p.get("height"),
                referer=p.get("referer"),
                headers=p.get("headers") or {},
                provider=p.get("provider") or doc.get("provider"),
                provider_page_id=p.get("provider_page_id"),
                source_url=p.get("source_url") or p.get("image_url"),
                fetched_at=p.get("fetched_at") or doc.get("fetched_at"),
                expires_at=p.get("expires_at") or doc.get("expires_at"),
            )
            for i, p in enumerate(pages_raw)
            if (p.get("image_url") or p.get("source_url"))
        ]
        cp = ChapterPages(
            images=[p.image_url for p in pages],
            provider=doc.get("provider") or "cache",
            chapter_number=doc.get("chapter_number"),
            chapter_name=doc.get("chapter_name"),
            pages=pages,
            fetched_at=doc.get("fetched_at"),
            expires_at=doc.get("expires_at"),
            referer=doc.get("referer"),
        )
    elif images:
        cp = ChapterPages.from_urls(
            list(images),
            provider=doc.get("provider") or "cache",
            chapter_number=doc.get("chapter_number"),
            chapter_name=doc.get("chapter_name"),
            referer=doc.get("referer"),
            ttl_seconds=None,
        )
        cp.fetched_at = doc.get("fetched_at")
        cp.expires_at = doc.get("expires_at")
    else:
        return None
    return cp


def should_refetch(doc: dict[str, Any] | ChapterPages | None) -> bool:
    if doc is None:
        return True
    if isinstance(doc, ChapterPages):
        return doc.needs_refetch() or not doc.images
    cp = pages_from_cache_doc(doc)
    if cp is None or not cp.images:
        return True
    return cp.needs_refetch()


def cache_document(pages: ChapterPages, *, chapter_id: str | None = None) -> dict[str, Any]:
    d = pages.to_dict()
    d["chapter_id"] = chapter_id
    d["cached_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return d
