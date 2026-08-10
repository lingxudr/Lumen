# Lumen

Reader komik lokal / deployable.

## Struktur

```
lumen/
├── public/                 # Frontend (static)
│   ├── index.html
│   ├── css/main.css
│   └── js/
│       ├── app.js          # entry
│       ├── config.js       # pengaturan
│       ├── api.js          # HTTP client
│       ├── ui.js           # toast, loading, gambar
│       ├── utils.js
│       └── views/          # home, series, reader
├── server/app.py           # Dev server (Python)
├── api/                    # Vercel serverless proxy
├── docs/
├── vercel.json
└── package.json
```

## Pengembangan lokal

```bash
cd lumen
python3 -u server/app.py
```

Buka http://127.0.0.1:5050

## Deploy Vercel

1. Push repo ini ke GitHub (root = folder `lumen`)
2. Import di Vercel
3. Deploy

Atau: `npx vercel` dari folder `lumen`.

## Kembangkan fitur baru

| Yang diubah | File |
|-------------|------|
| Endpoint / base URL | `public/js/config.js` |
| Request API | `public/js/api.js` |
| List & search | `public/js/views/home.js` |
| Detail judul | `public/js/views/series.js` |
| Halaman baca | `public/js/views/reader.js` |
| Tampilan / tema | `public/css/main.css` |
| Markup | `public/index.html` |

Data selalu **live** dari API — refresh halaman = data terbaru.
