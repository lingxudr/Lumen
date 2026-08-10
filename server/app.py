#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lumen Reader — pure stdlib HTTP server + API proxy."""
import json
import os
import ssl
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "public"
API_BASE = "https://be.komikcast.cc"
HOST = os.environ.get("KC_HOST", "0.0.0.0")
PORT = int(os.environ.get("KC_PORT", "5050"))
UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36"
)
SSL_CTX = ssl.create_default_context()

# Short TTL cache for list API (hemat upstream)
API_CACHE = {}
API_CACHE_TTL = 45  # seconds
API_CACHE_MAX = 64

def cache_get(key):
    import time
    row = API_CACHE.get(key)
    if not row:
        return None
    body, exp = row
    if time.time() > exp:
        API_CACHE.pop(key, None)
        return None
    return body

def cache_set(key, body):
    import time
    if len(API_CACHE) >= API_CACHE_MAX:
        # drop oldest-ish: clear half
        for k in list(API_CACHE.keys())[: API_CACHE_MAX // 2]:
            API_CACHE.pop(k, None)
    API_CACHE[key] = (body, time.time() + API_CACHE_TTL)


def fetch(url, extra_headers=None, timeout=30):
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        "Origin": "https://v3.komikcast.fit",
        "Referer": "https://v3.komikcast.fit/",
    }
    if extra_headers:
        headers.update(extra_headers)
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
        return e.code, hdrs, body


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
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if extra_headers:
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
                try:
                    code, _, _ = fetch(API_BASE + "/", timeout=8)
                    return self.send_json(200, {"ok": True, "upstream": code})
                except Exception as e:
                    return self.send_json(200, {"ok": True, "upstream": str(e)})

            if path.startswith("/api/"):
                sub = path[len("/api/") :]
                # check-hotlink is POST only
                if sub.startswith("check-hotlink"):
                    return self.send_json(405, {"error": "POST only"})
                url = API_BASE + "/" + sub
                if parsed.query:
                    url += "?" + parsed.query
                # Cache GET list/detail singkat (bukan chapter images)
                cacheable = (
                    sub == "series"
                    or (sub.startswith("series/") and "/chapters/" not in sub)
                    or sub == "genres"
                )
                # chapter list boleh di-cache sebentar
                if "/chapters" in sub and not sub.rstrip("/").endswith("chapters"):
                    # single chapter: /series/x/chapters/99 — cache pendek juga OK
                    cacheable = True
                if sub.count("/chapters/") == 1:
                    cacheable = True  # single chapter JSON (urls only)
                cache_key = "GET " + url
                if cacheable:
                    hit = cache_get(cache_key)
                    if hit is not None:
                        return self.send_bytes(
                            200, hit, "application/json; charset=utf-8",
                            extra_headers={"X-Lumen-Cache": "HIT"},
                        )
                code, hdrs, body = fetch(url)
                ct = hdrs.get("content-type") or "application/json; charset=utf-8"
                extra = {}
                if cacheable and code == 200:
                    cache_set(cache_key, body)
                    extra["X-Lumen-Cache"] = "MISS"
                return self.send_bytes(code, body, ct, extra_headers=extra)

            if path == "/img":
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
                code, hdrs, body = fetch(
                    src,
                    extra_headers={
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                        "Referer": "https://v3.komikcast.fit/",
                    },
                )
                ct = hdrs.get("content-type") or "image/jpeg"
                return self.send_bytes(code, body, ct)

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
    print("=" * 60, flush=True)
    print("  Lumen Reader", flush=True)
    print("  -> http://127.0.0.1:%s" % PORT, flush=True)
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
