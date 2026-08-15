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
