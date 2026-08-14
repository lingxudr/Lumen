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
