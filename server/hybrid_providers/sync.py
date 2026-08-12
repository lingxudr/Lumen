#!/usr/bin/env python3
"""
SYNC JOB — update proaktif (bukan on-demand user).

Alur:
  KomikCast latest ──┐
                     ├── match manga ── merge metadata ── DB
  Komiku latest    ──┘
                     │
                     ▼
              per manga: incremental chapters
                     │
              last_synced_chapter → hanya chapter baru
                     │
                     ▼
              merge sources + dedup → MongoDB

Jalankan:
  cd lumen
  export MONGO_URI=...
  export MONGO_DB=lumen_comic
  python3 -m server.hybrid_providers.sync
  python3 -m server.hybrid_providers.sync --limit 30 --chapters
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.hybrid_providers import (  # noqa: E402
    KomikcastProvider,
    KomikuProvider,
    ProviderError,
    ProviderManager,
)
from server.hybrid_providers import mongo as mongo_cache  # noqa: E402
from server.hybrid_providers.chapter_dedup import (  # noqa: E402
    dedupe_provider_chapter_infos,
    merge_source_maps,
    normalize_chapter_key,
    parse_chapter_number,
    pick_better_name,
)
from server.hybrid_providers.models import ChapterInfo, MangaInfo  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_title(t: str | None) -> str:
    if not t:
        return ""
    s = t.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # buang suffix umum
    for junk in (" manhwa", " manga", " manhua", " comic"):
        if s.endswith(junk):
            s = s[: -len(junk)].strip()
    return s


def _chapter_key(num: float | None, name: str | None = None) -> str:
    if num is not None:
        if float(num).is_integer():
            return str(int(num))
        return str(num)
    if name:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)", name)
        if m:
            return m.group(1)
    return (name or "").strip().lower()


class SyncJob:
    def __init__(self, mgr: ProviderManager | None = None):
        self.mgr = mgr or ProviderManager(
            [KomikcastProvider(), KomikuProvider()]
        )
        self.stats: dict[str, Any] = {
            "started_at": None,
            "finished_at": None,
            "latest_fetched": {},
            "manga_upserted": 0,
            "chapters_added": 0,
            "chapters_updated": 0,
            "errors": [],
        }

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        limit: int = 40,
        sync_chapters: bool = True,
        max_manga_chapters: int = 25,
    ) -> dict[str, Any]:
        """
        Full sync cycle:
        1. latest per provider
        2. match + upsert manga catalog
        3. optional incremental chapter sync for top N manga
        """
        self.stats["started_at"] = _now().isoformat()
        db = mongo_cache.get_db()
        if db is None:
            self.stats["errors"].append(
                f"mongo unavailable: {mongo_cache.status()}"
            )
            self.stats["finished_at"] = _now().isoformat()
            return self.stats

        # ensure indexes for catalog
        self._ensure_indexes(db)

        # 1) fetch latest both providers
        latest_map: dict[str, list[MangaInfo]] = {}
        for p in self.mgr.providers:
            try:
                batch = p.get_latest(page=1, limit=limit)
                latest_map[p.name] = batch
                self.stats["latest_fetched"][p.name] = len(batch)
            except ProviderError as e:
                latest_map[p.name] = []
                self.stats["errors"].append(f"latest {p.name}: {e}")

        # 2) match by normalized title + slug
        groups = self._match_groups(latest_map)

        # 3) upsert manga docs
        for group in groups:
            try:
                self._upsert_manga(db, group)
                self.stats["manga_upserted"] += 1
            except Exception as e:
                self.stats["errors"].append(f"upsert manga: {e}")

        # 4) incremental chapters
        if sync_chapters:
            # prioritaskan yang baru muncul di latest
            targets = groups[:max_manga_chapters]
            for group in targets:
                try:
                    self._sync_chapters_incremental(db, group)
                except Exception as e:
                    title = group.get("title") or "?"
                    self.stats["errors"].append(f"chapters {title}: {e}")

        # meta job log
        try:
            db.sync_log.insert_one(
                {
                    "type": "sync_job",
                    "stats": self.stats,
                    "at": _now(),
                }
            )
        except Exception:
            pass

        self.stats["finished_at"] = _now().isoformat()
        return self.stats

    # ------------------------------------------------------------------
    # matching
    # ------------------------------------------------------------------

    def _match_groups(
        self, latest_map: dict[str, list[MangaInfo]]
    ) -> list[dict[str, Any]]:
        """
        Group entries from different providers that refer to the same manga.
        Key: normalized title, fallback slug.
        """
        buckets: dict[str, dict[str, Any]] = {}

        for provider, items in latest_map.items():
            for m in items:
                key = _norm_title(m.title) or (m.slug or "").lower()
                if not key:
                    continue
                g = buckets.get(key)
                if not g:
                    g = {
                        "key": key,
                        "title": m.title,
                        "sources": {},  # provider -> MangaInfo
                    }
                    buckets[key] = g
                # prefer longer title
                if m.title and len(m.title) > len(g.get("title") or ""):
                    g["title"] = m.title
                g["sources"][provider] = m

        # order: manga yang muncul di lebih banyak provider dulu, lalu by title
        groups = list(buckets.values())
        groups.sort(
            key=lambda g: (-len(g["sources"]), (g.get("title") or "").lower())
        )
        return groups

    # ------------------------------------------------------------------
    # manga catalog
    # ------------------------------------------------------------------

    def _upsert_manga(self, db, group: dict[str, Any]) -> None:
        sources = group["sources"]
        # merge metadata: priority order of providers in manager
        base: MangaInfo | None = None
        for p in self.mgr.providers:
            m = sources.get(p.name)
            if not m:
                continue
            if base is None:
                base = m
            else:
                base = self.mgr._merge_manga(base, m)

        if base is None:
            return

        slug_map = {
            name: info.source_slug or info.slug
            for name, info in sources.items()
        }
        # canonical slug: prefer komikcast then komiku
        canonical = (
            slug_map.get("komikcast")
            or slug_map.get("komiku")
            or base.slug
        )

        # latest chapter label from any source that has it
        latest_ch = None
        latest_ch_url = None
        updated_label = None
        for p in self.mgr.providers:
            m = sources.get(p.name)
            if not m:
                continue
            if m.latest_chapter and not latest_ch:
                latest_ch = m.latest_chapter
                latest_ch_url = m.latest_chapter_url
            if m.updated_label and not updated_label:
                updated_label = m.updated_label

        doc = {
            "canonical_slug": canonical,
            "title": base.title,
            "title_norm": _norm_title(base.title),
            "title_alt": base.title_alt,
            "synopsis": base.synopsis,
            "cover_url": base.cover_url,
            "author": base.author,
            "status": base.status,
            "type": base.type,
            "genres": list(base.genres or []),
            "rating": base.rating,
            "latest_chapter": latest_ch or base.latest_chapter,
            "latest_chapter_url": latest_ch_url or base.latest_chapter_url,
            "updated_label": updated_label or base.updated_label,
            "slug_map": slug_map,
            "providers": list(slug_map.keys()),
            "updated_at": _now(),
        }

        db.catalog.update_one(
            {"canonical_slug": canonical},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": _now()},
            },
            upsert=True,
        )

        # also mirror per-provider cache (reuse existing cache helpers)
        for name, info in sources.items():
            mongo_cache.cache_set_manga(
                name, info.source_slug or info.slug, info.to_dict()
            )

    # ------------------------------------------------------------------
    # chapters — incremental
    # ------------------------------------------------------------------

    def _sync_chapters_incremental(self, db, group: dict[str, Any]) -> None:
        sources: dict[str, MangaInfo] = group["sources"]
        slug_map = {
            name: (info.source_slug or info.slug)
            for name, info in sources.items()
        }
        canonical = (
            slug_map.get("komikcast")
            or slug_map.get("komiku")
            or group.get("title")
        )
        if not canonical:
            return

        state = db.sync_state.find_one({"canonical_slug": canonical}) or {}
        last_num = state.get("last_synced_chapter")
        # float or None
        try:
            last_num_f = float(last_num) if last_num is not None else None
        except (TypeError, ValueError):
            last_num_f = None

        # fetch chapter lists from each provider that has a slug
        by_provider: dict[str, list] = {}
        provider_checked: dict[str, str] = {}

        for p in self.mgr.providers:
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            try:
                chapters = p.get_chapters(src_slug)
            except ProviderError as e:
                self.stats["errors"].append(
                    f"get_chapters {p.name}/{src_slug}: {e}"
                )
                continue

            provider_checked[p.name] = _now().isoformat()

            # incremental filter: keep new chapters + few near the tip for source merge
            filtered = []
            for ch in chapters:
                num = parse_chapter_number(ch.name, ch.number)
                if last_num_f is not None and num is not None and num < last_num_f - 3:
                    continue
                filtered.append(ch)
            by_provider[p.name] = filtered

        # dedup lintas provider
        deduped = dedupe_provider_chapter_infos(by_provider)

        if not deduped:
            db.sync_state.update_one(
                {"canonical_slug": canonical},
                {
                    "$set": {
                        "provider_last_checked": provider_checked,
                        "last_synced_at": _now(),
                    }
                },
                upsert=True,
            )
            return

        existing = {
            str(d.get("key")): d
            for d in db.chapter_index.find({"canonical_slug": canonical})
        }

        max_num = last_num_f
        for entry in deduped:
            key = entry.get("key") or normalize_chapter_key(
                entry.get("number"), entry.get("name")
            )
            num = entry.get("number")
            srcs = entry.get("sources") or {}
            payload = {
                "canonical_slug": canonical,
                "key": key,
                "number": num,
                "name": entry.get("name"),
                "sources": srcs,
                "providers": list(srcs.keys()),
                "updated_at": _now(),
            }
            if key in existing:
                payload["sources"] = merge_source_maps(
                    existing[key].get("sources"), srcs
                )
                payload["providers"] = list(payload["sources"].keys())
                payload["name"] = pick_better_name(
                    existing[key].get("name"), entry.get("name")
                )
                db.chapter_index.update_one(
                    {"canonical_slug": canonical, "key": key},
                    {"$set": payload},
                )
                self.stats["chapters_updated"] += 1
            else:
                payload["created_at"] = _now()
                db.chapter_index.update_one(
                    {"canonical_slug": canonical, "key": key},
                    {"$set": payload},
                    upsert=True,
                )
                self.stats["chapters_added"] += 1

            if num is not None:
                try:
                    nf = float(num)
                    if max_num is None or nf > max_num:
                        max_num = nf
                except (TypeError, ValueError):
                    pass

        db.sync_state.update_one(
            {"canonical_slug": canonical},
            {
                "$set": {
                    "canonical_slug": canonical,
                    "title": group.get("title"),
                    "slug_map": slug_map,
                    "last_synced_chapter": max_num,
                    "last_synced_at": _now(),
                    "provider_last_checked": provider_checked,
                }
            },
            upsert=True,
        )

        # mirror cache per provider
        for p in self.mgr.providers:
            src_slug = slug_map.get(p.name)
            if not src_slug:
                continue
            items = []
            for entry in deduped:
                src = (entry.get("sources") or {}).get(p.name)
                if not src:
                    continue
                items.append(
                    {
                        "number": entry.get("number"),
                        "name": entry.get("name"),
                        "url": src.get("url"),
                        "source_chapter_id": src.get("source_chapter_id"),
                        "published_at": src.get("published_at"),
                        "provider": p.name,
                    }
                )
            if items:
                mongo_cache.cache_set_chapters(p.name, src_slug, items)

    def _ensure_indexes(self, db) -> None:
        from pymongo import ASCENDING

        db.catalog.create_index("canonical_slug", unique=True)
        db.catalog.create_index("title_norm")
        db.chapter_index.create_index(
            [("canonical_slug", ASCENDING), ("key", ASCENDING)],
            unique=True,
        )
        db.sync_state.create_index("canonical_slug", unique=True)
        db.sync_log.create_index([("at", ASCENDING)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lumen hybrid sync job")
    parser.add_argument("--limit", type=int, default=40, help="latest per provider")
    parser.add_argument(
        "--chapters",
        action="store_true",
        default=True,
        help="sync chapters incremental (default on)",
    )
    parser.add_argument(
        "--no-chapters",
        action="store_true",
        help="skip chapter sync",
    )
    parser.add_argument(
        "--max-manga",
        type=int,
        default=25,
        help="max manga for chapter sync this run",
    )
    args = parser.parse_args(argv)

    if not (os.environ.get("MONGO_URI") or os.environ.get("MONGODB_URI")):
        print("ERROR: set MONGO_URI", file=sys.stderr)
        return 1

    job = SyncJob()
    t0 = time.time()
    stats = job.run(
        limit=args.limit,
        sync_chapters=not args.no_chapters,
        max_manga_chapters=args.max_manga,
    )
    elapsed = time.time() - t0
    print("=== SYNC DONE ===")
    print(f"elapsed: {elapsed:.1f}s")
    print(f"latest_fetched: {stats.get('latest_fetched')}")
    print(f"manga_upserted: {stats.get('manga_upserted')}")
    print(f"chapters_added: {stats.get('chapters_added')}")
    print(f"chapters_updated: {stats.get('chapters_updated')}")
    errs = stats.get("errors") or []
    print(f"errors: {len(errs)}")
    for e in errs[:10]:
        print(f"  - {e}")
    return 0 if not errs else 0  # soft ok


if __name__ == "__main__":
    raise SystemExit(main())
