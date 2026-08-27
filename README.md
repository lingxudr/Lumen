# Lumen

**Lumen** is a modern manga / manhwa / manhua reader.

- **UI:** cinematic dark SPA (mobile-first)
- **Backend:** Python (`server/app.py`) on Railway
- **Source:** [VoraToon](https://v1.voratoon.com) REST + RSC (successor to KomikCast)
- **Edge:** Vercel static hosting + `/api` & `/img` proxy

**Live:** [www.v1lumen.my.id](https://www.v1lumen.my.id)

---

## Features

| Area | Details |
|------|---------|
| Catalog | Terbaru, Series Baru, Selesai, Browse, Populer |
| Search | Title search via Voratoon |
| Detail | Cover, synopsis, genres, chapter list |
| Reader | Vertical scroll, progress, WebP proxy, lazy load |
| Library | Bookmarks & history in `localStorage` |
| PWA | Installable, service worker shell cache |
| API docs | [`/lumenrest/docs`](https://www.v1lumen.my.id/lumenrest/docs) |

---

## Architecture

```
Browser (Vercel)
   │
   ├─ /          static SPA
   ├─ /api/*  →  Vercel proxy  →  Railway Python API
   └─ /img    →  Vercel edge   →  Railway WebP / origin CDN
                                      │
                                      ▼
                               api.voratoon.com
                               v1.voratoon.com (RSC)
```

**Single provider:** Voratoon only.  
Do not re-enable multi-provider page fallback without strict title/ID matching (wrong chapter images risk).

## SEO

- `sitemap.xml` / `robots.txt` on `www.v1lumen.my.id`
- Google verification file in `/public`
- SPA `setMeta` + JSON-LD `ComicSeries` on manga detail
- Shareable paths: `/latest`, `/popular`, `/search?q=`, `/manga/:slug`  
Legacy KomikCast / Komiku / Sanka / hybrid stacks were removed.

---

## Project layout

```
Lumen/
├── public/                 # Frontend
│   ├── index.html
│   ├── css/                # main.css, reader-v2.css
│   ├── js/                 # app, api, views, sw
│   └── lumenrest/docs.html
├── server/
│   ├── app.py              # HTTP API + static + /img
│   ├── boot.py
│   ├── security.py         # SSRF allowlist
│   ├── cache_policy.py
│   ├── cache_warmer.py
│   ├── db.py               # SQLite optional cache
│   ├── providers/
│   │   └── voratoon.py     # only provider
│   └── services/
│       └── manga_service.py
├── api/                    # Vercel serverless (proxy, img)
├── tests/
├── vercel.json
├── railway.toml
├── Dockerfile
└── requirements.txt
```

---

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -u server/boot.py
# → http://127.0.0.1:8080
```

```bash
npm test          # matching + SSRF unit tests
npm run check     # compileall + tests
```

---

## Public URL

**Live:** [https://www.v1lumen.my.id](https://www.v1lumen.my.id)

Env (Railway + Vercel):

```bash
LUMEN_PUBLIC_URL=https://www.v1lumen.my.id
```

## Deploy

| Layer | Platform | Entry |
|-------|----------|--------|
| Frontend + edge proxy | **Vercel** | `public/` + `api/` |
| Python API | **Railway** | `Dockerfile` / `server/boot.py` |

### Railway env

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `8080` | Listen port |
| `API_BASE` | `https://api.voratoon.com` | Upstream REST |
| `RATE_LIMIT_API` | `20` | API req/min/IP |
| `RATE_LIMIT_IMG` | `60` | Image req/min/IP |
| `WEBP_QUALITY` | auto | WebP encode quality |
| `CACHE_WARM_INTERVAL` | `300` | Warm cycle (seconds) |
| `CACHE_KEEPALIVE` | `1` | Ping list every ~45s |
| `WHATSAPP_PHONE` | — | No. WA + kode negara, mis. `62812...` (CallMeBot) |
| `WHATSAPP_APIKEY` | — | API key dari CallMeBot |
| `TELEGRAM_BOT_TOKEN` | — | Bot token Telegram (opsional) |
| `TELEGRAM_CHAT_ID` | — | Chat ID Telegram kamu |
| `DISCORD_WEBHOOK_URL` | — | Opsional webhook Discord |
| `VISIT_NOTIFY_COOLDOWN` | `300` | Detik antar notif per IP |

### Vercel env

| Variable | Purpose |
|----------|---------|
| `LUMEN_UPSTREAM` | Railway public URL |
| `LUMEN_UPSTREAM_HOST` | Host allowlist for proxy |

---

## Public API (examples)

Base (production): `https://www.v1lumen.my.id/api`

```http
GET /api/ping
GET /api/series?take=30&page=1&mode=newest
GET /api/series?mode=hot
GET /api/popular?take=20
GET /api/genres
GET /api/series/{slug}
GET /api/series/{slug}/chapters
GET /api/series/{slug}/chapters/{index}
GET /img?u={imageUrl}&fmt=webp&w=360
```

JSON responses include Lumen watermark fields (`creator`, `website`, `watermark`, `docs`).

Interactive docs: **/lumenrest/docs**

---

## Security

- Image proxy host **allowlist** + SSRF blocks (`localhost`, private IPs, `file://`)
- Rate limits per IP
- CSP + security headers on API responses
- No open proxy: `/api` only relative paths to upstream

---

## Ops notes

- **Railway trial** expires → backend offline while Vercel UI may still load. Upgrade or migrate before trial ends.
- Cold start: `/api/ping` + cache warmer + client wake on page load.
- After deploy: hard refresh once so service worker **v6+** picks up the new shell.

---

## License

Private / personal project unless otherwise stated.
