# Hybrid Providers — Komikcast + Komiku

Modul multi-provider untuk metadata, chapter list, dan playback gambar.

## Struktur

```
server/hybrid_providers/
├── base.py              # BaseProvider interface
├── models.py            # MangaInfo, ChapterInfo, ChapterPages
├── manager.py           # merge metadata / chapters / pages
├── schema.sql           # SQLite schema (manga, chapter, page_cache, ...)
├── demo.py
└── providers/
    ├── komikcast.py     # API be.komikcast.cc (+ images)
    └── komiku.py        # WP REST + HTML scrape
```

## Install

```bash
pip install -r requirements.txt
```

## Pemakaian

```python
from server.hybrid_providers import (
    KomikcastProvider,
    KomikuProvider,
    ProviderManager,
)

mgr = ProviderManager([
    KomikcastProvider(),  # priority 10
    KomikuProvider(),     # priority 20
])

# metadata (merge field kosong)
manga = mgr.get_manga({
    "komikcast": "dandadan",
    "komiku": "dandadan",
})

# chapter digabung + source per chapter
chapters = mgr.get_chapters_merged({
    "komikcast": "dandadan",
    "komiku": "dandadan",
})

# playback — coba provider by priority sampai dapat gambar
pages = mgr.get_pages(chapters[0])
print(pages.provider, len(pages.images), pages.images[0])
```

## Demo CLI

```bash
cd lumen
python3 -m server.hybrid_providers.demo
```

> Catatan: sesuaikan `sys.path` / package layout deploy Anda.  
> Modul ini berdiri sendiri di bawah `server/hybrid_providers`.

## Endpoint provider

### Komikcast
- `GET https://be.komikcast.cc/series?page=&take=&sort=updatedAt`
- `GET /series/{slug}`
- `GET /series/{slug}/chapters`
- `GET /series/{slug}/chapters/{index}` → `images[]`

### Komiku
- REST: `https://komiku.org/wp-json/wp/v2/manga` (+ taxonomy)
- HTML: detail, daftar chapter, gambar baca
- Taxonomy cache: lazy + disk (`.cache/`)

## DB schema

Lihat `schema.sql` — tabel:
`manga`, `manga_source`, `chapter`, `chapter_source`, `page_cache`, `sync_log`.


## MongoDB cache

Set env:

```bash
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=lumen_comic
python3 -m server.hybrid_providers.api
```

Collections: `latest`, `manga`, `chapters`, `pages`.

Tanpa `MONGO_URI`, API tetap jalan (langsung ke provider).

Cek: `GET /api/mongo` atau `GET /api/health`.
