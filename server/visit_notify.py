"""Visitor notify — Telegram / Discord / WhatsApp. Rate-limited, bot-filtered."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
WHATSAPP_PHONE = (os.environ.get("WHATSAPP_PHONE") or "").strip().lstrip("+")
WHATSAPP_APIKEY = (
    os.environ.get("WHATSAPP_APIKEY") or os.environ.get("CALLMEBOT_APIKEY") or ""
).strip()

COOLDOWN_SEC = int(os.environ.get("VISIT_NOTIFY_COOLDOWN", "300"))
MAX_PER_HOUR = int(os.environ.get("VISIT_NOTIFY_MAX_HOUR", "40"))
SKIP_BOTS = (os.environ.get("VISIT_NOTIFY_SKIP_BOTS") or "1").strip() not in (
    "0",
    "false",
    "no",
)

_BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|headless|phantom|selenium|puppeteer|"
    r"lighthouse|pagespeed|pingdom|uptimerobot|preview|facebookexternalhit|"
    r"twitterbot|whatsapp|telegram|discord|vercel-screenshot",
    re.I,
)

_lock = threading.Lock()
_last_ip: dict[str, float] = {}
_hour_bucket: list[float] = []


def enabled() -> bool:
    return bool(
        (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        or DISCORD_WEBHOOK_URL
        or (WHATSAPP_PHONE and WHATSAPP_APIKEY)
    )


def _is_bot(ua: str, lang: str, screen: str) -> bool:
    if not ua or ua == "-":
        return True
    if _BOT_UA.search(ua):
        return True
    # typical headless defaults
    if screen in ("800x600", "0x0") and "Headless" in ua:
        return True
    return False


def _allow(ip: str) -> bool:
    now = time.time()
    with _lock:
        global _hour_bucket, _last_ip
        _hour_bucket = [t for t in _hour_bucket if now - t < 3600]
        if len(_hour_bucket) >= MAX_PER_HOUR:
            return False
        last = _last_ip.get(ip, 0)
        if now - last < COOLDOWN_SEC:
            return False
        _last_ip[ip] = now
        _hour_bucket.append(now)
        if len(_last_ip) > 5000:
            cutoff = now - COOLDOWN_SEC
            _last_ip = {k: v for k, v in _last_ip.items() if v >= cutoff}
        return True


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_html(path: str, ip: str, ref: str, lang: str, screen: str, ua: str) -> str:
    short_ua = ua if len(ua) <= 80 else ua[:77] + "…"
    ref_line = ref if ref and ref != "-" else "langsung"
    return (
        "👁 <b>Pengunjung baru</b>\n"
        "━━━━━━━━━━━━\n"
        f"📄 <b>Halaman</b>\n<code>{_esc(path)}</code>\n\n"
        f"🌐 <b>IP</b>  <code>{_esc(ip)}</code>\n"
        f"🔗 <b>Dari</b>  {_esc(ref_line)}\n"
        f"🗣 <b>Bahasa</b>  {_esc(lang)}\n"
        f"📱 <b>Layar</b>  {_esc(screen)}\n\n"
        f"<i>{_esc(short_ua)}</i>"
    )


def _format_plain(path: str, ip: str, ref: str, lang: str, screen: str, ua: str) -> str:
    short_ua = ua if len(ua) <= 80 else ua[:77] + "…"
    ref_line = ref if ref and ref != "-" else "langsung"
    return (
        "👁 Pengunjung baru\n"
        "————————————\n"
        f"Halaman: {path}\n"
        f"IP: {ip}\n"
        f"Dari: {ref_line}\n"
        f"Bahasa: {lang}\n"
        f"Layar: {screen}\n"
        f"{short_ua}"
    )


def _send_telegram(html: str, plain: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = json.dumps(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        # fallback plain if HTML rejected
        body2 = json.dumps(
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": plain,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req2 = urllib.request.Request(
            url, data=body2, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req2, timeout=10) as resp:
            resp.read()


def _send_discord(plain: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    body = json.dumps({"content": plain[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _send_whatsapp(plain: str) -> None:
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        return
    q = urllib.parse.urlencode(
        {
            "phone": WHATSAPP_PHONE,
            "text": plain[:900],
            "apikey": WHATSAPP_APIKEY,
        }
    )
    url = f"https://api.callmebot.com/whatsapp.php?{q}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "LumenVisitNotify/1"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def notify_visit(payload: dict[str, Any], *, ip: str = "unknown") -> dict[str, Any]:
    if not enabled():
        return {"ok": False, "reason": "notify_disabled"}

    path = str(payload.get("path") or "/")[:200]
    ref = str(payload.get("referrer") or payload.get("ref") or "-")[:200]
    ua = str(payload.get("ua") or "-")[:200]
    lang = str(payload.get("lang") or "-")[:40]
    screen = str(payload.get("screen") or "-")[:40]

    if SKIP_BOTS and _is_bot(ua, lang, screen):
        return {"ok": True, "skipped": "bot"}

    if not _allow(ip or "unknown"):
        return {"ok": True, "skipped": "cooldown_or_rate"}

    html = _format_html(path, ip, ref, lang, screen, ua)
    plain = _format_plain(path, ip, ref, lang, screen, ua)

    def _run():
        for name, fn in (
            ("telegram", lambda: _send_telegram(html, plain)),
            ("discord", lambda: _send_discord(plain)),
            ("whatsapp", lambda: _send_whatsapp(plain)),
        ):
            try:
                fn()
            except Exception as e:
                print(f"visit_notify {name}:", e, flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "queued": True}
