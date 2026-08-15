# Keamanan Lumen

## Ancaman yang ditangani

| Ancaman | Mitigasi |
|---------|----------|
| Open proxy / SSRF via `/img` atau `/api` | Allowlist host, blok `localhost` / IP privat / link-local / cloud metadata |
| Abuse API | Rate limit 20 req/menit (API), 60 (gambar) per IP |
| Clickjacking | `X-Frame-Options: SAMEORIGIN` |
| MIME sniffing | `X-Content-Type-Options: nosniff` |
| Leak referrer | `Referrer-Policy: strict-origin-when-cross-origin` |
| Fitur browser tak perlu | `Permissions-Policy` (camera/mic/geo off) |
| Redirect SSRF di edge | `redirect: manual` di `api/proxy.js` |

## Allowlist gambar (`server/security.py`)

Hanya host yang berakhiran / cocok dengan daftar (contoh):

- `cdn.voratoon.com`, `cvr.voratoon.id`
- CDN legacy (jika masih ada URL lama)

URL di luar allowlist → **403**.

## Rate limit

```text
RATE_LIMIT_API=20   # metadata
RATE_LIMIT_IMG=60   # chapter images
window = 60 detik
```

Respons: `429` + `Retry-After` + header `X-RateLimit-*`.

## Header keamanan (setiap respons HTML/API)

```text
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

Vercel juga mengirim `X-Content-Type-Options` global (`vercel.json`).

## Yang tidak dilakukan

- Tidak ada login/password server-side (belum)
- Tidak menyimpan kartu / data sensitif
- CSP ketat penuh belum diaktifkan (SPA + banyak CDN gambar) — bisa ditambah bertahap

## Melaporkan masalah

Buka issue di repo dengan label `security` (jangan publikasikan exploit detail sebelum diperbaiki).


## Content-Security-Policy (CSP)

Header `Content-Security-Policy` dikirim dari **Railway** (`server/app.py`) dan **Vercel** (`vercel.json`).

### Kebijakan saat ini

```http
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: blob: https://cdn.voratoon.com https://cvr.voratoon.id
           https://*.voratoon.com https://*.voratoon.id https://*.my.id https://*.shngm.id;
  font-src 'self' data:;
  connect-src 'self' https://api.voratoon.com https://v1.voratoon.com
              https://*.up.railway.app https://*.vercel.app;
  media-src 'self' blob:;
  object-src 'none';
  base-uri 'self';
  form-action 'self';
  frame-ancestors 'self';
  worker-src 'self';
  manifest-src 'self';
  upgrade-insecure-requests
```

### Arti tiap direktif

| Direktif | Nilai | Alasan |
|----------|-------|--------|
| `default-src` | `'self'` | Default ketat: hanya origin Lumen |
| `script-src` | `'self' 'unsafe-inline'` | Bundle `/js/*` + `onclick` / inline boot script di HTML |
| `style-src` | `'self' 'unsafe-inline'` | CSS file + atribut `style=""` di reader/UI |
| `img-src` | self + CDN VoraToon (+ legacy) | Cover/chapter; banyak lewat `/img` proxy (`'self'`) |
| `connect-src` | self + API + Railway/Vercel | `fetch` ke `/api/*` dan upstream |
| `object-src` | `'none'` | Blok Flash/plugin |
| `frame-ancestors` | `'self'` | Setara anti-clickjack (selaras `X-Frame-Options`) |
| `base-uri` / `form-action` | `'self'` | Cegah injeksi `<base>` / form ke domain asing |
| `upgrade-insecure-requests` | — | Paksa HTTP → HTTPS untuk subresource |

### Trade-off

- `'unsafe-inline'` pada script/style masih diperlukan karena SPA memakai event handler HTML dan sedikit style inline.
- Penguatan berikutnya: pindah onclick ke `addEventListener`, hapus inline style, lalu CSP tanpa `'unsafe-inline'` (+ nonce/hash bila perlu).
- Report-Only mode bisa dipakai dulu: header `Content-Security-Policy-Report-Only` + endpoint report (belum diaktifkan).
