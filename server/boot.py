#!/usr/bin/env python3
"""Railway entrypoint."""
import os
import sys
import traceback

def main():
    # Railway domain kamu diarahkan ke 8080 — samakan
    port = os.environ.get("PORT") or "8080"
    # jika platform inject PORT aneh, tetap sediakan 8080
    try:
        port_i = int(port)
    except Exception:
        port_i = 8080
    os.environ["PORT"] = str(port_i)
    os.environ["HOST"] = "0.0.0.0"
    if not os.environ.get("DB_PATH"):
        os.environ["DB_PATH"] = "/tmp/lumen.db"

    print("=" * 50, flush=True)
    print("Lumen boot", flush=True)
    print("PORT=", os.environ["PORT"], flush=True)
    print("[boot] cold-start path", flush=True)
    print("HOST=", os.environ["HOST"], flush=True)
    print("DB_PATH=", os.environ.get("DB_PATH"), flush=True)
    print("API_BASE=", os.environ.get("API_BASE"), flush=True)
    print("=" * 50, flush=True)

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)  # repo root (contains server/)
    for p in (here, root):
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        import app as lumen_app
        lumen_app.main()
        return
    except Exception:
        traceback.print_exc()

    # fallback mini server
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok":true,"mode":"fallback","path":"%s"}' % self.path.encode("utf-8", "ignore")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            print("req", args[0] if args else "", flush=True)

    print("FALLBACK server 0.0.0.0:%s" % port_i, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port_i), H).serve_forever()

if __name__ == "__main__":
    main()
