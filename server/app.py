#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lumen Reader — pure stdlib HTTP server + API proxy."""
import gzip
import json
import os
import ssl
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

import sys
_SERVER_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SERVER_DIR.parent
for _p in (_SERVER_DIR, _ROOT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
try:
    import db as lumen_db
except Exception as _db_imp_err:
    print("db import failed:", _db_imp_err, flush=True)

    class _DbStub:
        @staticmethod
        def init_db():
            return "(stub)"

        @staticmethod
        def stats():
            return {"error": "db unavailable"}

        @staticmethod
        def prune():
            return {"error": "db unavailable"}

        @staticmethod
        def save_series_response(body):
            return None

        @staticmethod
        def save_chapter_list(slug, body):
            return None

        @staticmethod
        def save_chapter_pages(slug, chapter, body):
            return None

        @staticmethod
        def get_manga(slug, max_age=0):
            return None

        @staticmethod
        def get_chapter_list(slug, max_age=0):
            return None

        @staticmethod
        def get_chapter_pages(slug, chapter, max_age=0):
            return None

        @staticmethod
        def wrap_manga_detail(payload_bytes):
            return payload_bytes

        @staticmethod
        def search_manga(query, limit=20):
            return []

    lumen_db = _DbStub()


# Legacy hybrid dimatikan — Lumen Voratoon-first (api.voratoon.com / RSC)
_HYBRID_OK = False
KomikuProvider = None  # type: ignore
VoratoonLegacyProvider = None  # type: ignore

try:
    from server import sanka_fallback  # type: ignore
except Exception:
    try:
        import sanka_fallback  # type: ignore
    except Exception:
        sanka_fallback = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "public"
def _resolve_api_base() -> str:
    """KomikCast API host sudah mati (penerus: VoraToon). Abaikan env lama."""
    raw = (os.environ.get("API_BASE") or os.environ.get("KC_API_BASE") or "https://api.voratoon.com").strip().rstrip("/")
    if not raw:
        return "https://api.voratoon.com"
    low = raw.lower()
    # Host legacy yang sering DNS fail di Railway
    if "komikcast" in low or "be.komikcast" in low:
        print("[config] API_BASE legacy komikcast diabaikan → api.voratoon.com", flush=True)
        return "https://api.voratoon.com"
    return raw

API_BASE = _resolve_api_base()
HOST = os.environ.get("HOST") or os.environ.get("KC_HOST") or "0.0.0.0"
PORT = int(os.environ.get("PORT") or os.environ.get("KC_PORT") or "8080")
UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
)
SSL_CTX = ssl.create_default_context()

# Branding / watermark on public API JSON
LUMEN_WATERMARK = {
    "creator": "Lumen",
    "website": "https://lumen-delta-lyart.vercel.app",
    "watermark": "Powered by Lumen Reader · lumen-delta-lyart.vercel.app",
    "docs": "https://lumen-delta-lyart.vercel.app/lumenrest/docs",
}


def stamp_lumen_payload(obj):
    """Inject Lumen watermark fields into dict JSON responses."""
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for k, v in LUMEN_WATERMARK.items():
        if k == "watermark":
            out[k] = v
        elif k not in out or out.get(k) in (None, "", []):
            out[k] = v
    return out


def stamp_lumen_json_bytes(body: bytes) -> bytes:
    try:
        text = body.decode("utf-8")
        data = json.loads(text)
    except Exception:
        return body
    if isinstance(data, dict):
        data = stamp_lumen_payload(data)
    elif isinstance(data, list):
        data = {
            **LUMEN_WATERMARK,
            "status": 200,
            "data": data,
        }
    else:
        return body
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


_health_hits = 0

import threading
import time
from collections import defaultdict, deque

# ── Tiered API cache + tag invalidation (see cache_policy.py) ────────
IMG_CACHE = {}
IMG_CACHE_MAX = 96
IMG_CACHE_TTL = 24 * 3600  # 24h in-process
_CACHE_LOCK = threading.Lock()

try:
    from cache_policy import (  # type: ignore
        cache_get as _policy_get,
        cache_set as _policy_set,
        ttl_for as _policy_ttl,
        invalidate as cache_invalidate,
        invalidate_series as cache_invalidate_series,
        invalidate_list as cache_invalidate_list,
        flush_all as cache_flush_all,
        stats as cache_stats,
        generation as cache_generation,
        bump_generation as cache_bump_generation,
    )
except Exception:
    from server.cache_policy import (  # type: ignore
        cache_get as _policy_get,
        cache_set as _policy_set,
        ttl_for as _policy_ttl,
        invalidate as cache_invalidate,
        invalidate_series as cache_invalidate_series,
        invalidate_list as cache_invalidate_list,
        flush_all as cache_flush_all,
        stats as cache_stats,
        generation as cache_generation,
        bump_generation as cache_bump_generation,
    )

# path context for cache_set (thread-local-ish via key parse)
def _warmer_status():
    try:
        try:
            from cache_warmer import status as _ws
        except Exception:
            from server.cache_warmer import status as _ws
        return _ws()
    except Exception as e:
        return {"error": str(e)}


def _ttl_for(sub_path):
    soft, hard = _policy_ttl(sub_path)
    return hard  # backward compat: hard TTL


def cache_get(key, allow_stale=True):
    hit = _policy_get(key, allow_stale=allow_stale)
    if hit is None:
        return None
    body, meta = hit
    return body, meta.get("age_left_hard", 0), meta.get("hard_ttl") or meta.get("age_left_hard", 0), meta


def cache_set(key, body, ttl, sub_path=None):
    # ttl arg = hard; derive soft as min(ttl, soft_default)
    sp = sub_path or ""
    if not sp and key.startswith("GET "):
        # extract path after host-ish
        try:
            from urllib.parse import urlparse
            u = urlparse(key[4:].strip())
            sp = (u.path or "").lstrip("/")
            if u.query:
                sp = sp  # tags ignore query
        except Exception:
            sp = ""
    soft, hard = _policy_ttl(sp)
    if ttl and ttl > 0:
        hard = int(ttl)
    _policy_set(key, body, sp, soft=soft, hard=hard)


def img_cache_get(key):
    with _CACHE_LOCK:
        row = IMG_CACHE.get(key)
        if not row:
            return None
        body, exp, ct = row
        if time.time() > exp:
            IMG_CACHE.pop(key, None)
            return None
        return body, ct


def img_cache_set(key, body, content_type):
    with _CACHE_LOCK:
        if len(body) > 2_500_000:
            return
        if len(IMG_CACHE) >= IMG_CACHE_MAX:
            for k in list(IMG_CACHE.keys())[: IMG_CACHE_MAX // 2]:
                IMG_CACHE.pop(k, None)
        ttl = IMG_CACHE_TTL
        if content_type and "webp" in content_type.lower():
            ttl = max(ttl, 48 * 3600)  # WebP stays warmer
        IMG_CACHE[key] = (body, time.time() + ttl, content_type)


# ── Rate limit per IP (sliding window 60s) ────────────────────────────
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://cdn.voratoon.com https://cvr.voratoon.id "
    "https://*.voratoon.com https://*.voratoon.id https://*.my.id https://*.shngm.id; "
    "font-src 'self' data:; "
    "connect-src 'self' https://api.voratoon.com https://v1.voratoon.com "
    "https://*.up.railway.app https://*.vercel.app; "
    "media-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "worker-src 'self'; "
    "manifest-src 'self'; "
    "upgrade-insecure-requests"
)

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_API = int(os.environ.get("RATE_LIMIT_API", "20"))   # metadata API / menit
RATE_LIMIT_IMG = int(os.environ.get("RATE_LIMIT_IMG", "60"))  # gambar: lebih longgar agar reader tidak 429
_RATE = defaultdict(deque)
_RATE_LOCK = threading.Lock()


def rate_allow(ip, bucket, limit):
    now = time.time()
    key = "%s:%s" % (bucket, ip or "unknown")
    with _RATE_LOCK:
        q = _RATE[key]
        cutoff = now - RATE_LIMIT_WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            reset = int(max(1, RATE_LIMIT_WINDOW - (now - q[0]))) if q else RATE_LIMIT_WINDOW
            return False, 0, reset
        q.append(now)
        return True, max(0, limit - len(q)), RATE_LIMIT_WINDOW


def client_ip(handler):
    xff = handler.headers.get("X-Forwarded-For") or handler.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return handler.client_address[0] if handler.client_address else "unknown"




def convert_to_webp(body, max_width=None, quality=None):
    """Re-encode image bytes to WebP. Returns (bytes, content_type) or None.

    Optimizations:
    - Skip tiny payloads
    - Adaptive quality by source size
    - Prefer smaller output (fallback to original if larger)
    - Optional downscale via max_width
    """
    try:
        from io import BytesIO
        from PIL import Image
    except Exception:
        return None
    if not body or len(body) < 200:
        return None
    if quality is None:
        try:
            quality = int(os.environ.get("WEBP_QUALITY") or "0")
        except Exception:
            quality = 0
    if not quality:
        # Adaptive: larger originals → slightly lower quality
        n = len(body)
        if n > 1_500_000:
            quality = 70
        elif n > 700_000:
            quality = 75
        else:
            quality = 80
    try:
        im = Image.open(BytesIO(body))
        im.load()
        # Already WebP and no resize needed → keep original
        fmt0 = (getattr(im, "format", None) or "").upper()
        if max_width:
            try:
                max_width = int(max_width)
            except Exception:
                max_width = None
        need_resize = bool(max_width and im.width > max_width)
        if fmt0 == "WEBP" and not need_resize:
            return body, "image/webp"
        if need_resize:
            h = max(1, int(im.height * (max_width / float(im.width))))
            im = im.resize((max_width, h), Image.Resampling.LANCZOS)
        # Flatten palette / weird modes → RGB/RGBA for reliable WebP
        if im.mode in ("P", "LA"):
            im = im.convert("RGBA")
        elif im.mode == "CMYK":
            im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in (im.getbands() or ()) else "RGB")
        # Drop alpha if fully opaque (smaller encode)
        if im.mode == "RGBA":
            extrema = im.getchannel("A").getextrema()
            if extrema == (255, 255):
                im = im.convert("RGB")
        buf = BytesIO()
        # method=4 balance; method=0 on retry path via quality-only calls still ok
        save_kw = {"format": "WEBP", "quality": int(quality), "method": 4}
        im.save(buf, **save_kw)
        data = buf.getvalue()
        # Prefer original only when source was already WebP and re-encode grew
        if fmt0 == "WEBP" and not need_resize and len(data) >= len(body):
            return body, "image/webp"
        # Always return WebP when encode succeeded (client asked fmt=webp)
        return data, "image/webp"
    except Exception as e:
        print("webp convert fail:", e, flush=True)
        return None


def fetch(url, extra_headers=None, timeout=12, retries=0):
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        "Origin": "https://v1.voratoon.com",
        "Referer": "https://v1.voratoon.com/",
    }
    if extra_headers:
        headers.update(extra_headers)
    last_err = None
    for attempt in range(retries + 1):
        req = Request(url, headers=headers, method="GET")
        try:
            resp = urlopen(req, timeout=timeout, context=SSL_CTX)
            try:
                body = resp.read()
                status = getattr(resp, "status", 200)
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return status, hdrs, body
            finally:
                resp.close()
        except HTTPError as e:
            body = e.read() if e.fp else b""
            hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
            # retry only transient 5xx
            if e.code >= 500 and attempt < retries:
                time.sleep(0.35 * (attempt + 1))
                last_err = e
                continue
            return e.code, hdrs, body
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.35 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err
    return 502, {}, b""


def mime(path):
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".webmanifest": "application/manifest+json",
        ".manifest": "application/manifest+json",
        ".ico": "image/x-icon",
        ".gif": "image/gif",
    }.get(path.suffix.lower(), "application/octet-stream")


def _parse_api_sub(sub: str):
    """Return (kind, slug, chapter) for series routes."""
    s = (sub or "").split("?")[0].strip("/")
    parts = [p for p in s.split("/") if p]
    if not parts or parts[0] != "series":
        return None, None, None
    if len(parts) == 1:
        return "list", None, None
    slug = parts[1]
    if len(parts) == 2:
        return "detail", slug, None
    if len(parts) == 3 and parts[2] == "chapters":
        return "chapters", slug, None
    if len(parts) >= 4 and parts[2] == "chapters":
        return "pages", slug, parts[3]
    return None, slug, None


def _persist_upstream(sub: str, body: bytes):
    kind, slug, chapter = _parse_api_sub(sub)
    try:
        if kind == "list" or kind == "detail":
            lumen_db.save_series_response(body)
        elif kind == "chapters" and slug:
            lumen_db.save_chapter_list(slug, body)
        elif kind == "pages" and slug and chapter is not None:
            lumen_db.save_chapter_pages(slug, chapter, body)
    except Exception:
        traceback.print_exc()


def _db_stats_safe():
    try:
        return lumen_db.stats()
    except Exception as e:
        return {"error": str(e)}


def _db_fallback(sub: str):
    """Delegate SQLite fallback ke services.manga_service."""
    try:
        from services.manga_service import sqlite_fallback
    except Exception:
        try:
            from server.services.manga_service import sqlite_fallback
        except Exception:
            return None
    return sqlite_fallback(sub)






def _norm_title_key(t):
    if not t:
        return ""
    import re
    s = str(t).lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _komiku_to_series_item(m):
    """Map MangaInfo legacy → bentuk item series frontend."""
    import re

    slug = m.slug or m.source_slug or ""
    chapters = []
    ch_name = m.latest_chapter or ""
    ch_num = None
    if ch_name:
        mm = re.search(r"([0-9]+(?:\.[0-9]+)?)", ch_name)
        if mm:
            try:
                ch_num = float(mm.group(1))
            except ValueError:
                ch_num = None
        chapters.append(
            {
                "id": None,
                "createdAt": None,
                "data": {
                    "index": int(ch_num)
                    if ch_num is not None and float(ch_num).is_integer()
                    else ch_num,
                    "title": ch_name,
                    "slug": None,
                },
                "provider": "komiku",
                "updated_label": m.updated_label,
            }
        )
    total_ch = None
    if ch_num is not None:
        total_ch = int(ch_num) if float(ch_num).is_integer() else ch_num
    return {
        "id": m.source_id or slug,
        "data": {
            "title": m.title,
            "nativeTitle": m.title_alt,
            "slug": slug,
            "coverImage": m.cover_url,
            "author": m.author,
            "rating": m.rating,
            "status": (m.status or "").lower() if m.status else None,
            "format": (m.type or "").lower() if m.type else None,
            "type": "mirror",
            "genreIds": [],
            "isHot": False,
            "totalChapters": total_ch,
            "provider": "komiku",
            "latestChapterLabel": m.latest_chapter,
            "updatedLabel": m.updated_label,
        },
        "createdAt": None,
        "updatedAt": m.updated_label,
        "chapters": chapters,
        "provider": "komiku",
        "_source": "komiku",
    }


def _komikcast_from_mongo_catalog(take=20):
    """Fallback saat api.voratoon.com 503 — pakai catalog Mongo jika ada."""
    out = []
    try:
        from server.hybrid_providers import mongo as mongo_cache

        db = mongo_cache.get_db()
        if db is None:
            return out
        cur = (
            db.catalog.find({"providers": "komikcast"})
            .sort("updated_at", -1)
            .limit(take)
        )
        for d in cur:
            slug = (d.get("slug_map") or {}).get("komikcast") or d.get(
                "canonical_slug"
            )
            if not slug:
                continue
            ch_label = d.get("latest_chapter")
            out.append(
                {
                    "id": slug,
                    "data": {
                        "title": d.get("title"),
                        "slug": slug,
                        "coverImage": d.get("cover_url"),
                        "status": (d.get("status") or "").lower() or None,
                        "format": (d.get("type") or "").lower() or None,
                        "provider": "voratoon",
                        "latestChapterLabel": ch_label,
                        "updatedLabel": d.get("updated_label"),
                        "totalChapters": None,
                    },
                    "chapters": [],
                    "provider": "voratoon",
                    "_source": "voratoon_mongo",
                }
            )
    except Exception:
        pass
    return out


def build_hybrid_newest(page=1, take=20):
    """
    Gabungan terbaru (legacy hybrid path; prefer Voratoon RSC di manga_service).
    Round-robin dedup by title. KC down → fallback Mongo / KU saja.
    """
    take = max(1, min(int(take or 20), 50))
    page = max(1, int(page or 1))
    items = []
    errors = []
    kc_live = False

    # Upstream API (retry lebih agresif)
    try:
        url = f"{API_BASE}/series?page={page}&take={take}&sort=updatedAt"
        code, hdrs, body = fetch(url, timeout=6, retries=1)
        if code == 200 and body:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            for it in payload.get("data") or []:
                if isinstance(it, dict):
                    it = dict(it)
                    it["_source"] = "voratoon"
                    it["provider"] = "voratoon"
                    if isinstance(it.get("data"), dict):
                        it["data"] = dict(it["data"])
                        it["data"]["provider"] = "voratoon"
                    items.append(it)
            kc_live = bool(payload.get("data"))
        else:
            errors.append(f"voratoon: HTTP {code}")
    except Exception as e:
        errors.append(f"voratoon: {e}")

    # Fallback Mongo catalog jika KC API mati
    if not kc_live and page <= 1:
        fb = _komikcast_from_mongo_catalog(take=take)
        if fb:
            items.extend(fb)
            errors.append("voratoon: using mongo fallback")
        else:
            errors.append("voratoon: unavailable (no mongo fallback)")

    # Komiku homepage #Terbaru (paling akurat untuk "baru diupdate")
    if _HYBRID_OK and KomikuProvider is not None and page <= 1:
        try:
            ku = KomikuProvider()
            batch = ku.get_latest(limit=max(take, 20))
            for m in batch:
                items.append(_komiku_to_series_item(m))
        except Exception as e:
            errors.append(f"komiku: {e}")

    kc_list = [
        it
        for it in items
        if (it.get("provider") or (it.get("data") or {}).get("provider"))
        == "voratoon"
    ]
    ku_list = [
        it
        for it in items
        if (it.get("provider") or (it.get("data") or {}).get("provider"))
        == "komiku"
    ]

    seen = set()
    merged = []

    def _key(it):
        d = it.get("data") or {}
        return _norm_title_key(d.get("title") or "") or (
            d.get("slug") or ""
        ).lower()

    def _add(it):
        k = _key(it)
        if not k or k in seen:
            return False
        seen.add(k)
        merged.append(it)
        return True

    # Round-robin; jika satu provider kosong, ambil penuh dari yang lain
    i = j = 0
    while len(merged) < take and (i < len(kc_list) or j < len(ku_list)):
        if i < len(kc_list):
            _add(kc_list[i])
            i += 1
        if len(merged) >= take:
            break
        if j < len(ku_list):
            _add(ku_list[j])
            j += 1

    # Jika masih kurang (mis. dedup ketat), isi sisa dari KU lalu KC
    if len(merged) < take:
        for it in ku_list[j:] + kc_list[i:]:
            if len(merged) >= take:
                break
            _add(it)

    return {
        "status": 200,
        "message": "Hybrid newest (voratoon)",
        "data": merged,
        "meta": {
            "source": "hybrid",
            "page": page,
            "take": take,
            "total": len(merged),
            "voratoon": len(
                [x for x in merged if x.get("provider") == "voratoon"]
            ),
            "komiku": len([x for x in merged if x.get("provider") == "komiku"]),
            "voratoon_live": kc_live,
            "errors": errors,
            "providers": ["voratoon"],
        },
    }


def _sanka_fallback_for_sub(sub: str, qs=None) -> bytes | None:
    """Delegate ke services.manga_service (provider logic keluar dari app.py)."""
    try:
        from services.manga_service import sanka_fallback
    except Exception:
        try:
            from server.services.manga_service import sanka_fallback
        except Exception as e:
            print("manga_service import fail:", e, flush=True)
            return None
    return sanka_fallback(sub, qs)





class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        try:
            sys_stderr = __import__("sys").stderr
            sys_stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
            sys_stderr.flush()
        except Exception:
            pass

    def send_bytes(self, code, body, content_type="text/plain; charset=utf-8", extra_headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = str(body).encode("utf-8")
        try:
            extra_headers = dict(extra_headers or {})
            # Watermark public JSON API payloads (always for application/json)
            try:
                ct_l = (content_type or "").lower()
                if body and isinstance(body, (bytes, bytearray)) and "json" in ct_l:
                    raw = bytes(body).lstrip()
                    # skip stamp for tiny health/ping payloads
                    if raw[:1] in (b"{", b"[") and len(raw) > 80 and b'"pong"' not in raw[:40]:
                        body = stamp_lumen_json_bytes(raw)
            except Exception as _wm_err:
                print("watermark stamp skip:", _wm_err, flush=True)
            # Security headers (baseline)
            extra_headers.setdefault("X-Content-Type-Options", "nosniff")
            extra_headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            extra_headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            extra_headers.setdefault(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=()",
            )
            extra_headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            if "Cache-Control" not in extra_headers:
                extra_headers["Cache-Control"] = "no-store"

            # Gzip JSON/text if client accepts and payload worth it
            ae = (self.headers.get("Accept-Encoding") or "").lower()
            use_gzip = False and (
                "gzip" in ae
                and len(body) >= 512
                and (
                    "json" in (content_type or "")
                    or "text/" in (content_type or "")
                    or "javascript" in (content_type or "")
                    or "css" in (content_type or "")
                )
                and "Content-Encoding" not in extra_headers
            )
            if use_gzip:
                body = gzip.compress(body, compresslevel=6)
                extra_headers["Content-Encoding"] = "gzip"
                extra_headers["Vary"] = "Accept-Encoding"

            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            for k, v in extra_headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def send_json(self, code, obj):
        body = json.dumps(stamp_lumen_payload(obj), ensure_ascii=False).encode("utf-8")
        self.send_bytes(code, body, "application/json; charset=utf-8")

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def serve_file(self, path):
        try:
            path = path.resolve()
            static_root = STATIC.resolve()
            if not str(path).startswith(str(static_root)) or not path.is_file():
                return self.send_json(404, {"error": "file not found"})
            data = path.read_bytes()
            self.send_bytes(200, data, mime(path))
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def do_GET(self):
        try:
            print("GET", self.path[:120], flush=True)
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            qs = parse_qs(parsed.query or "")

            if path in ("/", "/index.html", "/hub.html"):
                return self.serve_file(STATIC / "index.html")

            if path in ("/comic.html", "/comic", "/reader"):
                return self.serve_file(STATIC / "comic.html")

            if path in ("/api-explorer.html", "/api-explorer"):
                return self.serve_file(STATIC / "api-explorer.html")

            if path in ("/lumenrest", "/lumenrest/", "/lumenrest/docs", "/lumenrest/docs/", "/lumenrest/docs.html"):
                return self.serve_file(STATIC / "lumenrest" / "docs.html")

            if path in ("/sw.js", "/manifest.webmanifest"):
                return self.serve_file(STATIC / path.lstrip("/"))

            # static assets from public/
            if path.startswith(("/css/", "/js/", "/assets/", "/static/")):
                rel = path.lstrip("/").replace("..", "")
                if path.startswith("/static/"):
                    rel = path[len("/static/"):].replace("..", "")
                return self.serve_file(STATIC / rel)

            if path in ("/health", "/healthz", "/ready"):
                # Instant liveness — no upstream probe (critical for Railway cold start)
                global _health_hits
                _health_hits += 1
                if _health_hits % 100 == 0:
                    try:
                        lumen_db.prune()
                    except Exception:
                        pass
                return self.send_json(
                    200,
                    {
                        "ok": True,
                        "ready": True,
                        "api_base": API_BASE,
                        "cache": {**cache_stats(), "img": len(IMG_CACHE)},
                        "cache_warmer": _warmer_status(),
                    },
                )

            if path in (
                "/api/providers/health",
                "/api/providers/health/",
                "/api/providers",
                "/api/providers/",
            ):
                try:
                    from services.manga_service import provider_status
                    return self.send_json(200, provider_status())
                except Exception:
                    from server.services.manga_service import provider_status
                    return self.send_json(200, provider_status())

            if path in ("/api/metrics", "/api/metrics/"):
                try:
                    from services.manga_service import provider_status
                    st = provider_status()
                except Exception as e:
                    st = {"error": str(e)}
                providers = st.get("providers") or []
                metrics = {
                    "ok": True,
                    "providers": [
                        {
                            "provider": r.get("provider"),
                            "status": r.get("status"),
                            "latency": r.get("latency_ms"),
                            "avg_latency": r.get("avg_latency_ms"),
                            "error_rate": (r.get("error_rate") or 0) / 100.0
                            if (r.get("error_rate") or 0) > 1
                            else r.get("error_rate"),
                            "last_check_ago_sec": r.get("last_check_ago_sec"),
                            "successes": r.get("successes"),
                            "failures": r.get("failures"),
                            "circuit_open": r.get("circuit_open"),
                            "last_error": r.get("last_error"),
                        }
                        for r in providers
                        if isinstance(r, dict)
                    ],
                    "cache": {**cache_stats(), "img": len(IMG_CACHE)},
                    "cache_warmer": _warmer_status(),
                    "db": _db_stats_safe(),
                }
                return self.send_json(200, metrics)


            if path.startswith("/api/"):
                sub = path[len("/api/") :]
                if sub.startswith("check-hotlink"):
                    return self.send_json(405, {"error": "POST only"})

                # Instant ping — minimal body for Railway health / cold-start probes
                if sub.split("?")[0].rstrip("/") == "ping":
                    body = b'{"ok":true,"pong":true}'
                    return self.send_bytes(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        extra_headers={
                            "Cache-Control": "no-store",
                            "X-Lumen-Ping": "1",
                        },
                    )

                if sub.split("?")[0].rstrip("/") in ("health", "status"):
                    deep = (qs.get("deep") or ["0"])[0] in ("1", "true", "yes")
                    providers = None
                    if deep:
                        try:
                            try:
                                from services.manga_service import provider_status
                            except Exception:
                                from server.services.manga_service import provider_status
                            providers = provider_status(deep=True)
                        except Exception as e:
                            providers = {"error": str(e)}
                    return self.send_json(
                        200,
                        {
                            "ok": True,
                            "api_base": API_BASE,
                            "cache": {**cache_stats(), "img": len(IMG_CACHE)},
                            "cache_warmer": _warmer_status(),
                            "db": _db_stats_safe(),
                            "providers": providers,
                            "rate_limit": {
                                "api": RATE_LIMIT_API,
                                "img": RATE_LIMIT_IMG,
                                "window": RATE_LIMIT_WINDOW,
                            },
                        },
                    )

                # Local SQLite search (tidak ke upstream)
                if sub.split("?")[0] in ("local/search", "local/search/"):
                    ip = client_ip(self)
                    ok, remaining, reset = rate_allow(ip, "api", RATE_LIMIT_API)
                    if not ok:
                        return self.send_json(429, {"error": "rate_limited", "retry_after": reset})
                    q = (qs.get("q") or qs.get("title") or [""])[0]
                    try:
                        limit = int((qs.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    items = lumen_db.search_manga(q, limit=min(50, max(1, limit)))
                    body = json.dumps(
                        {
                            "status": 200,
                            "message": "Local search",
                            "data": items,
                            "meta": {"source": "sqlite", "q": q, "total": len(items)},
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    return self.send_bytes(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        extra_headers={"X-Lumen-DB": "SEARCH", "Cache-Control": "public, max-age=30"},
                    )

                if sub.split("?")[0] in ("local/stats", "local/prune"):
                    if sub.startswith("local/prune"):
                        pruned = lumen_db.prune()
                        return self.send_json(200, {"ok": True, "db": pruned})
                    return self.send_json(200, {"ok": True, "db": lumen_db.stats()})

                ip = client_ip(self)
                ok, remaining, reset = rate_allow(ip, "api", RATE_LIMIT_API)
                rate_headers = {
                    "X-RateLimit-Limit": str(RATE_LIMIT_API),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(reset),
                }
                if not ok:
                    rate_headers["Retry-After"] = str(reset)
                    return self.send_bytes(
                        429,
                        json.dumps({"error": "rate_limited", "retry_after": reset}).encode(),
                        "application/json; charset=utf-8",
                        extra_headers=rate_headers,
                    )


                # Search local-first: ?title= / ?q= → SQLite dulu, baru upstream
                qsearch = (qs.get("title") or qs.get("q") or qs.get("search") or [""])[0].strip()
                if qsearch and (sub or "").split("?")[0].strip("/") == "series":
                    try:
                        limit = int((qs.get("take") or qs.get("limit") or ["20"])[0])
                    except Exception:
                        limit = 20
                    try:
                        items = lumen_db.search_manga(qsearch, limit=min(50, max(1, limit)))
                    except Exception:
                        items = []
                    if items:
                        body = json.dumps(
                            {
                                "status": 200,
                                "message": "Local search",
                                "data": items,
                                "meta": {
                                    "source": "sqlite_search",
                                    "total": len(items),
                                    "page": 1,
                                    "lastPage": 1,
                                    "q": qsearch,
                                },
                            },
                            ensure_ascii=False,
                        ).encode("utf-8")
                        extra = dict(rate_headers)
                        extra["X-Lumen-Cache"] = "LOCAL_SEARCH"
                        extra["Cache-Control"] = "public, max-age=120"
                        return self.send_bytes(
                            200, body, "application/json; charset=utf-8", extra_headers=extra
                        )

                # DB-first: Mongo canonical catalog untuk list series
                sub0 = (sub or "").split("?")[0].strip("/")
                if sub0 == "series":
                    try:
                        from services.manga_service import catalog_newest
                    except Exception:
                        try:
                            from server.services.manga_service import catalog_newest
                        except Exception:
                            catalog_newest = None
                    if catalog_newest:
                        try:
                            take = int((qs.get("take") or qs.get("limit") or ["20"])[0])
                        except Exception:
                            take = 20
                        cat = catalog_newest(take=take)
                        if cat:
                            extra = dict(rate_headers)
                            extra["X-Lumen-Cache"] = "MONGO"
                            extra["X-Lumen-DB"] = "CATALOG"
                            extra["Cache-Control"] = "public, max-age=60"
                            return self.send_bytes(
                                200, cat, "application/json; charset=utf-8", extra_headers=extra
                            )

                # Upstream proxy (Voratoon-first)
                url = API_BASE + "/" + sub
                if parsed.query:
                    url += "?" + parsed.query

                cache_key = "GET " + url
                ttl = _ttl_for(sub)

                # series/genres/chapters lewat provider dulu (hindari host mati / DNS noise)
                sub0 = (sub or "").split("?")[0].strip("/")
                if sub0 in ("series", "genres", "popular") or sub0.startswith("series/"):
                    sanka_body = _sanka_fallback_for_sub(sub, qs)
                    if sanka_body:
                        cache_set(cache_key, sanka_body, ttl, sub_path=sub)
                        extra = dict(rate_headers)
                        extra["X-Lumen-Cache"] = "VORATOON"
                        extra["X-Lumen-DB"] = "MISS"
                        extra["X-Lumen-Cache-TTL"] = str(ttl)
                        extra["Cache-Control"] = "public, max-age=%d" % min(120, ttl)
                        return self.send_bytes(
                            200, sanka_body, "application/json; charset=utf-8", extra_headers=extra
                        )

                hit = cache_get(cache_key, allow_stale=True)
                if hit is not None:
                    body, age_left, used_ttl, meta = hit
                    extra = dict(rate_headers)
                    extra["X-Lumen-Cache"] = "STALE" if meta.get("stale") else "HIT"
                    extra["X-Lumen-Cache-TTL"] = str(used_ttl)
                    extra["X-Lumen-Cache-Gen"] = str(meta.get("gen") or "")
                    extra["Cache-Control"] = "public, max-age=%d" % min(age_left, used_ttl or age_left)
                    return self.send_bytes(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        extra_headers=extra,
                    )

                try:
                    # retry: api.voratoon.com sering 503 sebentar
                    code, hdrs, body = fetch(url, timeout=6, retries=1)
                except Exception as e:
                    print("upstream error:", sub, e, flush=True)
                    fb = _db_fallback(sub)
                    if fb:
                        extra = dict(rate_headers)
                        extra["X-Lumen-Cache"] = "DB"
                        extra["X-Lumen-DB"] = "HIT"
                        extra["Cache-Control"] = "public, max-age=60"
                        return self.send_bytes(
                            200, fb, "application/json; charset=utf-8", extra_headers=extra
                        )
                    sanka_body = _sanka_fallback_for_sub(sub, qs)
                    if sanka_body:
                        cache_set(cache_key, sanka_body, ttl, sub_path=sub)
                        extra = dict(rate_headers)
                        extra["X-Lumen-Cache"] = "SANKA"
                        extra["X-Lumen-Cache-TTL"] = str(ttl)
                        extra["Cache-Control"] = "public, max-age=%d" % min(120, ttl)
                        return self.send_bytes(
                            200, sanka_body, "application/json; charset=utf-8", extra_headers=extra
                        )
                    return self.send_json(
                        200,
                        {
                            "status": 502,
                            "message": "Sumber sementara tidak terjangkau",
                            "error": "upstream_error",
                            "detail": str(e),
                            "data": [],
                            "meta": {"page": 1, "lastPage": 1, "total": 0},
                        },
                    )
                ct = hdrs.get("content-type") or "application/json; charset=utf-8"
                extra = dict(rate_headers)
                if code == 200:
                    cache_set(cache_key, body, ttl, sub_path=sub)
                    _persist_upstream(sub, body)
                    extra["X-Lumen-Cache"] = "MISS"
                    extra["X-Lumen-Cache-TTL"] = str(ttl)
                    extra["X-Lumen-DB"] = "WRITE"
                    extra["Cache-Control"] = "public, max-age=%d" % min(60, ttl)
                    return self.send_bytes(code, body, ct, extra_headers=extra)

                # upstream gagal → SQLite cache, lalu Sanka (sementara KC down)
                fb = _db_fallback(sub)
                if fb:
                    extra["X-Lumen-Cache"] = "DB"
                    extra["X-Lumen-DB"] = "HIT"
                    extra["Cache-Control"] = "public, max-age=60"
                    return self.send_bytes(
                        200, fb, "application/json; charset=utf-8", extra_headers=extra
                    )

                sanka_body = _sanka_fallback_for_sub(sub, qs)
                if sanka_body:
                    cache_set(cache_key, sanka_body, ttl, sub_path=sub)
                    extra["X-Lumen-Cache"] = "SANKA"
                    extra["X-Lumen-DB"] = "MISS"
                    extra["X-Lumen-Cache-TTL"] = str(ttl)
                    extra["Cache-Control"] = "public, max-age=%d" % min(120, ttl)
                    return self.send_bytes(
                        200, sanka_body, "application/json; charset=utf-8", extra_headers=extra
                    )

                # JSON error (bukan plain text 503)
                err = {
                    "status": code,
                    "error": "upstream_unavailable",
                    "message": "Provider & fallback tidak tersedia. Coba lagi nanti.",
                    "path": sub,
                }
                extra["X-Lumen-Cache"] = "BYPASS"
                extra["X-Lumen-DB"] = "MISS"
                return self.send_bytes(
                    200 if code >= 500 else code,
                    json.dumps(err, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                    extra_headers=extra,
                )

            if path == "/img":
                ip = client_ip(self)
                ok, remaining, reset = rate_allow(ip, "img", RATE_LIMIT_IMG)
                rate_headers = {
                    "X-RateLimit-Limit": str(RATE_LIMIT_IMG),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(reset),
                }
                if not ok:
                    rate_headers["Retry-After"] = str(reset)
                    return self.send_bytes(
                        429,
                        json.dumps({"error": "rate_limited", "retry_after": reset}).encode(),
                        "application/json; charset=utf-8",
                        extra_headers=rate_headers,
                    )

                src = (qs.get("u") or [""])[0].strip()
                try:
                    from security import validate_image_url, MAX_IMAGE_BYTES
                except Exception:
                    from server.security import validate_image_url, MAX_IMAGE_BYTES  # type: ignore
                ok_url, reason = validate_image_url(src)
                if not ok_url:
                    return self.send_json(403, {"error": "host not allowed", "reason": reason})

                want_webp = False
                fmt = ((qs.get("fmt") or [""])[0] or "").lower()
                accept = (self.headers.get("Accept") or "").lower()
                if fmt == "webp" or "image/webp" in accept:
                    want_webp = True
                max_w = (qs.get("w") or [None])[0]

                cache_key = src + ("|webp" if want_webp else "|raw") + ("|w=" + str(max_w) if max_w else "")
                cached = img_cache_get(cache_key)
                if cached is not None:
                    body, ct = cached
                    extra = dict(rate_headers)
                    extra["X-Lumen-Cache"] = "HIT"
                    extra["Cache-Control"] = "public, max-age=604800, s-maxage=2592000, stale-while-revalidate=604800"
                    extra["CDN-Cache-Control"] = "public, max-age=2592000, stale-while-revalidate=604800"
                    extra["X-Content-Type-Options"] = "nosniff"
                    if "webp" in (ct or ""):
                        extra["X-Lumen-Image"] = "webp"
                    return self.send_bytes(200, body, ct, extra_headers=extra)

                code, hdrs, body = fetch(
                    src,
                    extra_headers={
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        "Referer": "https://v1.voratoon.com/",
                    },
                    timeout=15,
                    retries=0,
                )
                ct = hdrs.get("content-type") or "image/jpeg"
                extra = dict(rate_headers)
                extra["X-Content-Type-Options"] = "nosniff"

                if code == 200 and body:
                    try:
                        from security import MAX_IMAGE_BYTES as _MAX_IMG
                    except Exception:
                        _MAX_IMG = 12 * 1024 * 1024
                    if len(body) > _MAX_IMG:
                        return self.send_json(502, {"error": "image_too_large", "bytes": len(body)})
                    # Prefer WebP when requested (convert JPEG/PNG; resize if w=)
                    is_webp = "webp" in (ct or "").lower() or src.lower().endswith(".webp")
                    if want_webp or max_w:
                        converted = convert_to_webp(body, max_width=max_w)
                        if not converted and want_webp:
                            # retry with more aggressive compression
                            converted = convert_to_webp(body, max_width=max_w, quality=72)
                        if converted:
                            body, ct = converted[0], converted[1]
                            is_webp = True
                            extra["X-Lumen-Image"] = "webp"
                        elif want_webp:
                            extra["X-Lumen-Image"] = "jpeg-fallback"
                            print("webp miss, serving original", (ct or "")[:40], len(body), flush=True)
                    if is_webp:
                        ct = ct if "webp" in (ct or "").lower() else "image/webp"
                        extra["X-Lumen-Image"] = extra.get("X-Lumen-Image") or "webp"

                    img_cache_set(cache_key, body, ct)
                    extra["X-Lumen-Cache"] = "MISS"
                    # WebP/static pages cache longer; browser may revalidate weekly
                    extra["Cache-Control"] = "public, max-age=604800, s-maxage=2592000, stale-while-revalidate=604800"
                    extra["CDN-Cache-Control"] = "public, max-age=2592000, stale-while-revalidate=604800"
                    extra["Vary"] = "Accept"
                else:
                    extra["X-Lumen-Cache"] = "BYPASS"
                return self.send_bytes(code, body, ct, extra_headers=extra)

            self.send_json(404, {"error": "not found", "path": path})
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json(500, {"error": str(e)})
            except Exception:
                pass

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            if path != "/api/check-hotlink":
                return self.send_json(404, {"error": "not found"})

            data = self.read_json()
            urls = data.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            urls = [u for u in urls if isinstance(u, str) and u.startswith("http")][:8]
            if not urls:
                return self.send_json(400, {"error": "provide urls: string[]"})

            strategies = [
                ("no_referer", None),
                ("empty_referer", ""),
                ("voratoon_referer", "https://v1.voratoon.com/"),
                ("foreign_referer", "https://example.com/"),
            ]
            results = []
            for url in urls:
                entry = {"url": url, "tests": []}
                for name, referer in strategies:
                    headers = {"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"}
                    if referer is not None:
                        headers["Referer"] = referer
                    try:
                        req = Request(url, headers=headers, method="GET")
                        resp = urlopen(req, timeout=12, context=SSL_CTX)
                        try:
                            chunk = resp.read(2048)
                            status = getattr(resp, "status", 200)
                            ct = resp.headers.get("Content-Type", "") or ""
                        finally:
                            resp.close()
                        is_image = status == 200 and (
                            ct.startswith("image/")
                            or chunk[:3] == b"\xff\xd8\xff"
                            or chunk[:8] == b"\x89PNG\r\n\x1a\n"
                            or chunk[:4] == b"RIFF"
                            or b"WEBP" in chunk[:16]
                        )
                        entry["tests"].append(
                            {
                                "strategy": name,
                                "status": status,
                                "content_type": ct,
                                "bytes_sample": len(chunk),
                                "ok_image": bool(is_image),
                            }
                        )
                    except HTTPError as e:
                        entry["tests"].append(
                            {
                                "strategy": name,
                                "status": e.code,
                                "ok_image": False,
                                "error": str(e.reason),
                            }
                        )
                    except Exception as e:
                        entry["tests"].append(
                            {
                                "strategy": name,
                                "status": None,
                                "ok_image": False,
                                "error": str(e),
                            }
                        )

                oks = {t["strategy"]: t.get("ok_image") for t in entry["tests"]}
                if oks.get("no_referer") or oks.get("empty_referer") or oks.get("foreign_referer"):
                    entry["verdict"] = "open" if oks.get("voratoon_referer") else "mixed"
                elif oks.get("voratoon_referer"):
                    entry["verdict"] = "hotlink_protected"
                else:
                    entry["verdict"] = "blocked"
                results.append(entry)

            self.send_json(200, {"results": results})
        except Exception as e:
            traceback.print_exc()
            try:
                self.send_json(500, {"error": str(e)})
            except Exception:
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    """Bind HTTP ASAP, then warm cache in background (cold-start friendly)."""
    import threading as _th
    import time as _t

    global PORT, HOST
    HOST = "0.0.0.0"
    try:
        PORT = int(os.environ.get("PORT") or os.environ.get("KC_PORT") or "8080")
    except Exception:
        PORT = 8080

    # Lightweight DB init (SQLite) — keep off critical path if it ever blocks
    db_path = "(disabled)"

    def _init_db_bg():
        nonlocal db_path
        try:
            db_path = lumen_db.init_db()
            print("[boot] db ready:", db_path, flush=True)
        except Exception as e:
            print("db init failed:", e, flush=True)

    _th.Thread(target=_init_db_bg, name="lumen-db-init", daemon=True).start()

    ThreadingHTTPServer.allow_reuse_address = True
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print("bind failed on %s: %s — trying 8080" % (PORT, e), flush=True)
        PORT = 8080
        server = ThreadingHTTPServer((HOST, PORT), Handler)

    print("=" * 60, flush=True)
    print("  Lumen Reader READY (cold-start optimized)", flush=True)
    print("  bind: %s:%s" % (HOST, PORT), flush=True)
    print("  api: %s" % API_BASE, flush=True)
    print("=" * 60, flush=True)
    print("listening on %s:%s" % (HOST, PORT), flush=True)

    def _start_warmer():
        try:
            try:
                from cache_warmer import start_background_warmer, warm_once
            except Exception:
                from server.cache_warmer import start_background_warmer, warm_once  # type: ignore

            def _prewarm_fetch(sub, params):
                try:
                    return _sanka_fallback_for_sub(
                        sub, {k: [str(v)] for k, v in (params or {}).items()}
                    )
                except Exception:
                    return None

            start_background_warmer(fetch_json=_prewarm_fetch)

            # Immediate warm right after listen (parallel to traffic)
            try:
                print("[boot] immediate warm start", flush=True)
                st = warm_once(fetch_json=_prewarm_fetch)
                print("[boot] immediate warm done", st, flush=True)
            except Exception as e:
                print("[boot] immediate warm error", e, flush=True)
        except Exception as _warm_err:
            print("cache_warmer start failed:", _warm_err, flush=True)

    _th.Thread(target=_start_warmer, name="lumen-warm-boot", daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("bye", flush=True)
    finally:
        server.server_close()



if __name__ == "__main__":
    main()
