# Lumen Backend Architecture

## Arah resmi: DB-first

```
Provider (Komikcast / Sanka / …)
        ↓
   Sync Worker (cron)
        ↓
 Normalizer + Canonical Match + Deduper
        ↓
   Canonical DB (Mongo catalog + chapter_index)
        ↓
   Lumen API (baca DB dulu)
        ↓
   Web Reader
```

Bukan: setiap request user → hit provider langsung.

## Source of truth

| Layer | Peran | SoT? |
|-------|--------|------|
| **Canonical DB** (Mongo `catalog`, `chapter_index`) | Metadata + chapter index hasil sync | **YA — API reads** |
| **Provider live** | Input sync worker + emergency fallback | Input / fallback |
| **SQLite** | Edge cache pages di Railway | Cache lokal pages |

### Read path API

1. **Mongo catalog** (DB-first) bila `MONGO_URI` + data ada  
2. SQLite cache  
3. Provider live (Sanka saat KC down; Komikcast saat hidup)

### Write path

Sync Worker saja yang menulis catalog / chapter_index.
User request **tidak** men-trigger scrape massal.

## Canonical matching (`services/canonical_match.py`)

Urutan:
1. exact provider ID  
2. normalized slug  
3. normalized title  
4. alternative title overlap  
5. author + fuzzy title  
6. fuzzy similarity ≥ **0.92**  
7. manual alias  

Proteksi: `solo-leveling` ≠ `solo-leveling-ragnarok` (slug suffix distinct).

## Chapter dedup

`chapter_dedup.normalize_chapter_key` + merge `sources` per chapter.
Incremental: `last_synced_chapter` di `sync_state`.

## Menjalankan sync

```bash
export MONGO_URI=mongodb+srv://...
export MONGO_DB=lumen_comic
python3 -m server.hybrid_providers.sync --limit 40 --chapters
```

Cron contoh: setiap 15–30 menit.

## Struktur

```
server/
├── app.py
├── services/
│   ├── manga_service.py      # read path + fallback
│   └── canonical_match.py    # cluster / alias / fuzzy
├── providers/sanka.py
├── hybrid_providers/
│   ├── sync.py               # Sync Worker
│   ├── chapter_dedup.py
│   ├── mongo.py
│   └── manager.py
└── db.py                     # SQLite pages cache
```

## Sync levels (penting)

| Level | Isi | Kapan |
|-------|-----|--------|
| 1 Catalog | title, slug, cover, author, status, genres | schedule latest |
| 2 Chapters | number, title, date, provider URL | incremental vs last_synced |
| 3 Pages | image URLs | **on-demand reader/prefetch only** |

Concurrency: `SyncQueue(max_workers=5)` + per-provider rate_limit.
