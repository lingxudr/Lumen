# Architecture

Lumen is **Voratoon-first**.

```
Client → Vercel (static + /api proxy + /img) → Railway (app.py) → Voratoon API/RSC
```

## Provider

Only `server/providers/voratoon.py` is used for catalog, detail, chapters, and pages.

`services/manga_service.provider_fallback` (alias `sanka_fallback`) is the single resolve entry from `app.py`.

## Caching

- In-process list/detail cache (`cache_policy`) with soft/hard TTL
- Image memory cache + CDN headers (`s-maxage`)
- Background warmer + keepalive thread

## Removed

KomikCast, Komiku, Sanka, and `hybrid_providers` are not part of the runtime.
