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
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))
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

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "public"
API_BASE = (os.environ.get("API_BASE") or os.environ.get("KC_API_BASE") or "https://be.komikcast.cc").rstrip("/")
HOST = os.environ.get("KC_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("KC_PORT") or "5050")
UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
)
SSL_CTX = ssl.create_default_context()
_health_hits = 0

import threading
import time
from collections import defaultdict, deque

# ── Tiered API cache (agresif, hemat upstream) ───────────────────────
# list/search: 60s | series detail: 5m | chapter list: 3m
# single chapter: 15m | genres: 30m | images: 2h
API_CACHE = {}
IMG_CACHE = {}
API_CACHE_MAX = 256
IMG_CACHE_MAX = 48
IMG_CACHE_TTL = 2 * 3600
_CACHE_LOCK = threading.Lock()


def _ttl_for(sub_path):
    s = (sub_path or "").split("?")[0].strip("/")
    if s == "genres" or s.startswith("genres/"):
        return 30 * 60
    if "/chapters/" in s:
        return 15 * 60
    if s.endswith("/chapters"):
        return 3 * 60
    if s.startswith("series/") and "/chapters" not in s:
        return 5 * 60
    if s == "series":
        return 60
    return 90


def cache_get(key):
    with _CACHE_LOCK:
        row = API_CACHE.get(key)
        if not row:
            return None
        body, exp, ttl = row
        if time.time() > exp:
            API_CACHE.pop(key, None)
            return None
        return body, max(0, int(exp - time.time())), ttl


def cache_set(key, body, ttl):
    with _CACHE_LOCK:
        if len(API_CACHE) >= API_CACHE_MAX:
            now = time.time()
            for k, v in list(API_CACHE.items()):
                if now > v[1]:
                    API_CACHE.pop(k, None)
            if len(API_CACHE) >= API_CACHE_MAX:
                for k in list(API_CACHE.keys())[: API_CACHE_MAX // 2]:
                    API_CACHE.pop(k, None)
        API_CACHE[key] = (body, time.time() + ttl, ttl)


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
        IMG_CACHE[key] = (body, time.time() + IMG_CACHE_TTL, content_type)


# ── Rate limit per IP (sliding window 60s) ────────────────────────────
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_API = int(os.environ.get("RATE_LIMIT_API", "120"))
RATE_LIMIT_IMG = int(os.environ.get("RATE_LIMIT_IMG", "90"))
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



def fetch(url, extra_headers=None, timeout=25, retries=1):
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        "Origin": "https://v3.komikcast.fit",
        "Referer": "https://v3.komikcast.fit/",
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

    kind, slug, chapter = _parse_api_sub(sub)
    try:
        if kind == "detail" and slug:
            raw = lumen_db.get_manga(slug)
            if raw:
                return lumen_db.wrap_manga_detail(raw)
        if kind == "chapters" and slug:
            return lumen_db.get_chapter_list(slug)
        if kind == "pages" and slug and chapter is not None:
            return lumen_db.get_chapter_pages(slug, chapter)
    except Exception:
        traceback.print_exc()
    return None



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
            if "Cache-Control" not in extra_headers:
                extra_headers["Cache-Control"] = "no-store"

            # Gzip JSON/text if client accepts and payload worth it
            ae = (self.headers.get("Accept-Encoding") or "").lower()
            use_gzip = (
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
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
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
            parsed = urlparse(self.path)
            path = unquote(parsed.path or "/")
            qs = parse_qs(parsed.query or "")

            if path in ("/", "/index.html"):
                return self.serve_file(STATIC / "index.html")

            if path in ("/sw.js", "/manifest.webmanifest"):
                return self.serve_file(STATIC / path.lstrip("/"))

            # static assets from public/
            if path.startswith(("/css/", "/js/", "/assets/", "/static/")):
                rel = path.lstrip("/").replace("..", "")
                if path.startswith("/static/"):
                    rel = path[len("/static/"):].replace("..", "")
                return self.serve_file(STATIC / rel)

            if path == "/health":
                global _health_hits
                _health_hits += 1
                if _health_hits % 50 == 0:
                    try:
                        lumen_db.prune()
                    except Exception:
                        pass
                return self.send_json(
                    200,
                    {
                        "ok": True,
                        "api_base": API_BASE,
                        "cache": {"api": len(API_CACHE), "img": len(IMG_CACHE)},
                        "db": _db_stats_safe(),
                        "rate_limit": {
                            "api": RATE_LIMIT_API,
                            "img": RATE_LIMIT_IMG,
                            "window": RATE_LIMIT_WINDOW,
                        },
                    },
                )


            if path.startswith("/api/"):
                sub = path[len("/api/") :]
                if sub.startswith("check-hotlink"):
                    return self.send_json(405, {"error": "POST only"})

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

                url = API_BASE + "/" + sub
                if parsed.query:
                    url += "?" + parsed.query

                cache_key = "GET " + url
                ttl = _ttl_for(sub)
                hit = cache_get(cache_key)
                if hit is not None:
                    body, age_left, used_ttl = hit
                    extra = dict(rate_headers)
                    extra["X-Lumen-Cache"] = "HIT"
                    extra["X-Lumen-Cache-TTL"] = str(used_ttl)
                    extra["Cache-Control"] = "public, max-age=%d" % min(age_left, used_ttl)
                    return self.send_bytes(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        extra_headers=extra,
                    )

                try:
                    code, hdrs, body = fetch(url)
                except Exception as e:
                    fb = _db_fallback(sub)
                    if fb:
                        extra = dict(rate_headers)
                        extra["X-Lumen-Cache"] = "DB"
                        extra["X-Lumen-DB"] = "HIT"
                        extra["Cache-Control"] = "public, max-age=60"
                        return self.send_bytes(
                            200, fb, "application/json; charset=utf-8", extra_headers=extra
                        )
                    return self.send_json(502, {"error": "upstream_error", "detail": str(e)})
                ct = hdrs.get("content-type") or "application/json; charset=utf-8"
                extra = dict(rate_headers)
                if code == 200:
                    cache_set(cache_key, body, ttl)
                    _persist_upstream(sub, body)
                    extra["X-Lumen-Cache"] = "MISS"
                    extra["X-Lumen-Cache-TTL"] = str(ttl)
                    extra["X-Lumen-DB"] = "WRITE"
                    extra["Cache-Control"] = "public, max-age=%d" % min(60, ttl)
                    return self.send_bytes(code, body, ct, extra_headers=extra)

                # upstream gagal → coba SQLite
                fb = _db_fallback(sub)
                if fb:
                    extra["X-Lumen-Cache"] = "DB"
                    extra["X-Lumen-DB"] = "HIT"
                    extra["Cache-Control"] = "public, max-age=60"
                    return self.send_bytes(
                        200, fb, "application/json; charset=utf-8", extra_headers=extra
                    )
                extra["X-Lumen-Cache"] = "BYPASS"
                extra["X-Lumen-DB"] = "MISS"
                return self.send_bytes(code, body, ct, extra_headers=extra)

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
                if not (src.startswith("http://") or src.startswith("https://")):
                    return self.send_json(400, {"error": "missing or invalid u"})
                allowed = (
                    "imgkc1.my.id",
                    "komikcast.fit",
                    "komikcast.com",
                    "minio.",
                    "cdn.",
                    "sv1.",
                    "sv2.",
                    "sv3.",
                )
                if not any(a in src for a in allowed):
                    return self.send_json(403, {"error": "host not allowed"})

                cached = img_cache_get(src)
                if cached is not None:
                    body, ct = cached
                    extra = dict(rate_headers)
                    extra["X-Lumen-Cache"] = "HIT"
                    extra["Cache-Control"] = "public, max-age=3600"
                    return self.send_bytes(200, body, ct, extra_headers=extra)

                code, hdrs, body = fetch(
                    src,
                    extra_headers={
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        "Referer": "https://v3.komikcast.fit/",
                    },
                )
                ct = hdrs.get("content-type") or "image/jpeg"
                extra = dict(rate_headers)
                if code == 200 and body:
                    img_cache_set(src, body, ct)
                    extra["X-Lumen-Cache"] = "MISS"
                    extra["Cache-Control"] = "public, max-age=3600"
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
                ("komikcast_referer", "https://v3.komikcast.fit/"),
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
                    entry["verdict"] = "open" if oks.get("komikcast_referer") else "mixed"
                elif oks.get("komikcast_referer"):
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
    db_path = "(disabled)"
    try:
        db_path = lumen_db.init_db()
        try:
            lumen_db.prune()
        except Exception as e:
            print("prune skip: %s" % e, flush=True)
    except Exception as e:
        print("db init failed (continue without db): %s" % e, flush=True)
    print("=" * 60, flush=True)
    print("  Lumen Reader", flush=True)
    print("  -> http://0.0.0.0:%s" % PORT, flush=True)
    print("  db: %s" % db_path, flush=True)
    print("  api: %s" % API_BASE, flush=True)
    print("=" * 60, flush=True)
    ThreadingHTTPServer.allow_reuse_address = True
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print("Gagal bind port %s: %s" % (PORT, e), flush=True)
        raise SystemExit(1)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
