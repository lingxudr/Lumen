# Deployment

## Vercel ≠ Python backend

| Platform | Peran |
|----------|--------|
| **Vercel** | Frontend (`public/`) + serverless `api/*.js` |
| **Railway/Render** | `server/app.py` — ProviderManager, Mongo, SQLite, Sanka |
| **MongoDB Atlas** | Canonical catalog (DB-first) |

`server/app.py` tidak otomatis jalan di Vercel.

## Opsi A (disarankan)

```
Vercel   → Frontend + edge proxy
Railway  → Python API + sync worker
MongoDB  → catalog
```

## Scripts

```bash
npm run dev
npm run check
npm test
```


## Komiku IP ban (Railway)

Jika log menunjukkan `403 Forbidden` ke komiku.org dari Railway:

1. Deploy frontend ke Vercel (endpoint `/api/komiku` proxy).
2. Di Railway Variables, set:

```
KOMIKU_PROXY_BASE=https://lumen-delta-lyart.vercel.app
```

Railway fetch Komiku lewat IP Vercel (bukan IP yang di-ban).
