#!/usr/bin/env python3
"""Demo hybrid: Komikcast + Komiku (tanpa Shinigami)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# pastikan package bisa diimport saat dijalankan langsung
# jalan dari root lumen: python3 -m server.hybrid_providers.demo
# atau: python3 server/hybrid_providers/demo.py
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from server.hybrid_providers import (
        KomikcastProvider,
        KomikuProvider,
        ProviderManager,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from hybrid_providers import (
        KomikcastProvider,
        KomikuProvider,
        ProviderManager,
    )



def main() -> None:
    mgr = ProviderManager(
        [
            KomikcastProvider(),
            KomikuProvider(),
        ]
    )

    print("== Health ==")
    for p in mgr.providers:
        print(p.health())

    print("\n== Latest (merged search pool) ==")
    latest = mgr.get_latest(limit=5)
    for m in latest:
        print(f"  [{m.provider}] {m.title} ({m.slug})")

    # contoh slug map — sesuaikan setelah punya mapping DB
    # di sini demo pakai slug yang sama jika kebetulan match
    print("\n== Komiku detail sample ==")
    komiku = mgr.by_name("komiku")
    assert komiku
    k_latest = komiku.get_latest(limit=1)
    if not k_latest:
        print("  (kosong)")
        return
    sample = k_latest[0]
    print(f"  slug={sample.slug} title={sample.title}")

    detail = komiku.get_manga(sample.slug)
    if detail:
        print(
            json.dumps(
                {
                    "title": detail.title,
                    "status": detail.status,
                    "genres": detail.genres,
                    "cover": (detail.cover_url or "")[:80],
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    chapters = komiku.get_chapters(sample.slug)
    print(f"  chapters={len(chapters)}")
    if chapters:
        ch = chapters[0]
        print(f"  latest chapter: {ch.name} number={ch.number}")
        try:
            pages = komiku.get_pages(ch)
            print(f"  pages={len(pages.images)} source={pages.provider}")
            print(f"  first={pages.images[0] if pages.images else None}")
        except Exception as e:
            print(f"  pages error: {e}")

    # merge chapters demo (hanya komiku di map)
    print("\n== Merged chapters (komiku only in map) ==")
    merged = mgr.get_chapters_merged({"komiku": sample.slug})
    for row in merged[:5]:
        srcs = ", ".join(row["sources"].keys())
        print(f"  {row['name']} | sources=[{srcs}]")

    print("\nDone.")


if __name__ == "__main__":
    main()
