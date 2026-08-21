# Deployment

## Railway

1. Connect GitHub repo `lingxudr/Lumen`
2. Use Dockerfile builder
3. Set `API_BASE=https://api.voratoon.com`
4. Set `LUMEN_UPSTREAM` on Vercel to this service URL

Healthcheck: `GET /api/ping`

## Vercel

1. Root directory: repo root
2. Output: static from `public/` (or framework preset that serves `public`)
3. Env: `LUMEN_UPSTREAM`, `LUMEN_UPSTREAM_HOST`

## Checklist after deploy

- [ ] `GET /api/ping` → `{"ok":true,"pong":true}`
- [ ] `GET /api/series?take=2&mode=newest` → items + watermark
- [ ] `GET /api/series?mode=hot` → no `$attributes` / `modelOptions`
- [ ] Cover `/img?u=…&fmt=webp&w=360` → `image/webp`
