"""Visitor notify — Telegram (optional Discord). Rate-limited, non-blocking."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()

# min seconds between messages per IP
COOLDOWN_SEC = int(os.environ.get("VISIT_NOTIFY_COOLDOWN", "300"))  # 5 min
# global max messages per hour
MAX_PER_HOUR = int(os.environ.get("VISIT_NOTIFY_MAX_HOUR", "40"))

_lock = threading.Lock()
_last_ip: dict[str, float] = {}
_hour_bucket: list[float] = []


def enabled() -> bool:
    return bool(
        (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) or DISCORD_WEBHOOK_URL
    )


def _allow(ip: str) -> bool:
    now = time.time()
    with _lock:
        # prune hour bucket
        global _hour_bucket
        _hour_bucket = [t for t in _hour_bucket if now - t < 3600]
        if len(_hour_bucket) >= MAX_PER_HOUR:
            return False
        last = _last_ip.get(ip, 0)
        if now - last < COOLDOWN_SEC:
            return False
        _last_ip[ip] = now
        _hour_bucket.append(now)
        # bound map size
        if len(_last_ip) > 5000:
            cutoff = now - COOLDOWN_SEC
            _last_ip = {k: v for k, v in _last_ip.items() if v >= cutoff}
        return True


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def _send_discord(text: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    body = json.dumps({"content": text[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def notify_visit(payload: dict[str, Any], *, ip: str = "unknown") -> dict[str, Any]:
    """Fire-and-forget visitor alert. Returns status for API response."""
    if not enabled():
        return {"ok": False, "reason": "notify_disabled"}
    if not _allow(ip or "unknown"):
        return {"ok": True, "skipped": "cooldown_or_rate"}

    path = str(payload.get("path") or "/")[:200]
    ref = str(payload.get("referrer") or payload.get("ref") or "-")[:200]
    ua = str(payload.get("ua") or "-")[:160]
    lang = str(payload.get("lang") or "-")[:40]
    screen = str(payload.get("screen") or "-")[:40]

    lines = [
        "👁 Pengunjung Lumen",
        f"• Path: {path}",
        f"• IP: {ip}",
        f"• Ref: {ref}",
        f"• Lang: {lang}",
        f"• Screen: {screen}",
        f"• UA: {ua}",
    ]
    text = "\n".join(lines)

    def _run():
        try:
            _send_telegram(text)
        except Exception as e:
            print("visit_notify telegram:", e, flush=True)
        try:
            _send_discord(text)
        except Exception as e:
            print("visit_notify discord:", e, flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "queued": True}
