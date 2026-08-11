# Railway setup (Lumen proxy)

## Volume (wajib agar SQLite tidak hilang)
1. Railway → service **web** → **Variables** / **Settings**
2. **Volumes** → Add Volume
3. Mount path: `/data`
4. Redeploy

DB path default: `/data/lumen.db` (`DB_PATH`)

## Environment variables
| Key | Contoh | Fungsi |
|-----|--------|--------|
| `PORT` | (otomatis) | Port HTTP |
| `DB_PATH` | `/data/lumen.db` | Lokasi SQLite |
| `API_BASE` | `https://be.komikcast.cc` | Host API sumber (tanpa slash akhir) |
| `RATE_LIMIT_API` | `120` | Limit /api per IP / 60s |
| `RATE_LIMIT_IMG` | `90` | Limit /img per IP / 60s |

## Cek
- Health: `https://YOUR.up.railway.app/health`
- Local search: `/api/local/search?q=solo`
- Prune manual: `/api/local/prune`
- Stats: `/api/local/stats`
