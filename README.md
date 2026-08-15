# Lumen

Reader komik modern (frontend SPA + Python API). Sumber data utama: **VoraToon** (penerus KomikCast).

- Frontend: `https://lumen-delta-lyart.vercel.app`
- Backend: Railway (`server/app.py`)
- API sumber: `https://api.voratoon.com` + RSC `https://v1.voratoon.com`

## Fitur

- Katalog Terbaru / Series Baru / Selesai / Browse / Populer
- Detail manga + daftar chapter (float index aman)
- Reader V2 (glass header, progress, lazy load, WebP proxy)
- Cache bertingkat (soft/hard TTL ~5 menit untuk list)
- Rate limit per IP
- Image proxy anti-SSRF + allowlist host
- Favorit & riwayat (localStorage)

## Struktur

```
lumen/
├── public/                 # Frontend static
│   ├── index.html
│   ├── css/                # main.css, reader-v2.css
│   └── js/
│       ├── app.js
│       ├── config.js
│       ├── api.js
│       └── views/          # home, series, reader, library
├── server/
│   ├── app.py              # HTTP API + static
│   ├── security.py         # SSRF / host allowlist
│   ├── cache_policy.py
│   ├── cache_warmer.py
│   ├── providers/          # voratoon, …
│   └── services/
├── api/                    # Vercel edge proxy → Railway
├── docs/
├── vercel.json
├── railway.toml
└── requirements.txt
```

## Pengembangan lokal

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -u server/app.py
```

Buka `http://127.0.0.1:8080` (atau `PORT` di env).

## Deploy

| Layer | Platform | Catatan |
|-------|----------|---------|
| Frontend + `/api/*` proxy | Vercel | rewrite ke Railway |
| Python backend | Railway | `server/app.py` |
| DB opsional | MongoDB Atlas | catalog cache |

### Env penting (Railway)

| Variable | Default | Fungsi |
|----------|---------|--------|
| `PORT` | `8080` | bind port |
| `API_BASE` | `https://api.voratoon.com` | upstream REST (jangan `be.komikcast.cc`) |
| `RATE_LIMIT_API` | `20` | req/menit/IP untuk API |
| `RATE_LIMIT_IMG` | `60` | req/menit/IP untuk gambar |
| `MONGODB_URI` | — | opsional catalog |
| `WEBP_QUALITY` | auto | 0 = adaptif |

### Env Vercel

| Variable | Fungsi |
|----------|--------|
| `LUMEN_UPSTREAM` / `LUMEN_UPSTREAM_HOST` | URL host Railway backend |

## Keamanan

Ringkasan — detail di [`docs/SECURITY.md`](docs/SECURITY.md).

- **Anti-SSRF** pada `/img` dan proxy: hanya host allowlist, blok IP privat/metadata
- **Rate limit** sliding window per IP
- **Header**: `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`
- **Path proxy relatif** saja (tolak URL absolut di edge)
- Tidak menyimpan kredensial pengguna (favorit = localStorage)

## API Lumen (backend)

```text
GET /api/series?take=30&page=1&mode=newest
GET /api/series/{slug}
GET /api/series/{slug}/chapters
GET /api/series/{slug}/chapters/{index}
GET /api/genres
GET /api/health
GET /img?u=<encoded_url>&fmt=webp&w=480
```

Upstream VoraToon (referensi):

```text
GET https://api.voratoon.com/series
GET https://api.voratoon.com/series/{slug}/chapters/{index}
GET https://api.voratoon.com/genres
GET https://api.voratoon.com/popular
```

## Lisensi & etika

Proyek personal/edukasi. Hormati ToS sumber konten; jangan spam scrape.
Gunakan cache + rate limit yang sudah disetel.

## Dokumen lain

- [`docs/SECURITY.md`](docs/SECURITY.md) — keamanan
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arsitektur
- [`docs/RAILWAY.md`](docs/RAILWAY.md) — deploy Railway
