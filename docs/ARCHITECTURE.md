# Lumen Backend Architecture

## Masalah

`server/app.py` menumpuk routing + provider + fallback + DB + cache.
Sulit diuji dan diganti provider.

## Target layer

```
Route (Handler)
  → Service (services/manga_service.py)
  → Provider chain (Komikcast → SQLite → Sanka Shinigami → Sanka Komiku)
  → Repository (db.py / mongo opsional)
```

## Source of truth (production)

| Layer | Peran | SoT? |
|-------|--------|------|
| Provider live (Komikcast / Sanka) | Konten, chapter, gambar | **YA** |
| SQLite (`lumen.db`) | Read-through cache | Tidak (SWR) |
| MongoDB (`MONGO_URI`) | Catalog/sync opsional | Tidak |

Aturan:
1. Baca selalu coba provider dulu.
2. Provider 5xx → SQLite bila ada.
3. SQLite kosong → Sanka (Shinigami lalu Komiku-style).
4. Mongo tidak dipakai untuk pages kritis.
5. Tanpa MONGO_URI app tetap jalan.

## Struktur

```
server/
├── app.py
├── db.py
├── providers/sanka.py
├── services/manga_service.py
├── hybrid_providers/   # models, manager, sync, mongo
└── routes/             # next: pecah handler
```
