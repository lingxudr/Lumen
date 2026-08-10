# Deploy proxy ke Railway

## Kenapa
Vercel data-center IP diblokir Cloudflare di API sumber.
Proxy Python di Railway biasanya lolos.

## Langkah
1. Daftar https://railway.app (login GitHub)
2. New Project → Deploy from GitHub repo `lingxudr/Lumen`
3. Settings → generate domain (`.up.railway.app`)
4. Pastikan start command: `python3 -u server/app.py`
5. Copy URL public, mis. `https://lumen-production-xxxx.up.railway.app`
6. Edit `public/js/config.js` → ganti `RAILWAY_ORIGIN`
7. Commit + push → Vercel redeploy otomatis

## Test
```bash
curl https://YOUR.up.railway.app/health
curl "https://YOUR.up.railway.app/api/series?preset=rilisan_terbaru&take=1"
```
