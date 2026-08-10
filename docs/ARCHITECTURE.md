# Architecture

```
Browser
  │
  ├─ public/*          static assets
  │
  ├─ /api/*            proxy → upstream manga API
  ├─ /img?u=           image proxy
  └─ /api/check-hotlink
```

- **Local:** `server/app.py` handles all routes.
- **Vercel:** `api/*.js` serverless; static from `public/`.

Frontend modules (ESM):

```
app.js → views/* → api.js / ui.js / utils.js / config.js
```
