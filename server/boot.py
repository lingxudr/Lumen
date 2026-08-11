#!/usr/bin/env python3
"""Railway entrypoint — log env then start app."""
import os
import sys
import traceback

def main():
    port = os.environ.get("PORT") or os.environ.get("KC_PORT") or "8080"
    os.environ["PORT"] = str(port)
    os.environ.setdefault("HOST", "0.0.0.0")
    os.environ.setdefault("DB_PATH", "/tmp/lumen.db")
    print("=" * 50, flush=True)
    print("Lumen boot", flush=True)
    print("PORT=", os.environ.get("PORT"), flush=True)
    print("HOST=", os.environ.get("HOST"), flush=True)
    print("DB_PATH=", os.environ.get("DB_PATH"), flush=True)
    print("API_BASE=", os.environ.get("API_BASE"), flush=True)
    print("=" * 50, flush=True)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import app as lumen_app
        lumen_app.main()
    except Exception:
        traceback.print_exc()
        # Last resort: tiny health-only server so Railway is not 502 empty
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"ok":false,"error":"boot_failed"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        p = int(os.environ["PORT"])
        print("fallback health server on", p, flush=True)
        HTTPServer(("0.0.0.0", p), H).serve_forever()

if __name__ == "__main__":
    main()
