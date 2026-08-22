"""Visitor notify — Telegram / Discord / WhatsApp. Advanced bot detection + rate limit."""
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
SKIP_BOTS = (os.environ.get("VISIT_NOTIFY_SKIP_BOTS") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
# Score >= threshold → treat as bot (0–100)
BOT_SCORE_THRESHOLD = int(os.environ.get("VISIT_BOT_SCORE_THRESHOLD", "55"))

_BOT_UA = re.compile(
    r"(?:"
    r"bot|crawl|spider|slurp|headless|phantom|selenium|puppeteer|playwright|"
    r"lighthouse|pagespeed|pingdom|uptimerobot|statuscake|gtmetrix|"
    r"preview|facebookexternalhit|facebot|twitterbot|linkedinbot|pinterest|"
    r"whatsapp|telegram|discord|slackbot|skypeuri|"
    r"vercel-screenshot|chrome-lighthouse|googlebot|bingbot|yandex|baidu|"
    r"duckduck|semrush|ahrefs|mj12bot|dotbot|petalbot|bytespider|"
    r"python-requests|curl/|wget/|httpclient|java/|go-http|okhttp|"
    r"scrapy|aiohttp|httpx|libwww|perl|"
    r"monitor|checker|scan|archive\.org|wayback"
    r")",
    re.I,
)

# Datacenter / cloud AS hints in UA rarely, but IP prefixes known scanners
_CLOUD_IP_PREFIXES = (
    "34.", "35.", "104.196.", "104.197.",  # GCP sample
    "52.", "54.", "3.", "18.", "13.",  # AWS broad (heuristic only)
    "40.7", "40.8", "40.9",  # Azure sample
    "167.99.", "159.65.", "138.68.",  # DO
    "146.190.", "157.230.",
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


def bot_score(payload: dict[str, Any], *, ip: str = "", ua_header: str = "") -> tuple[int, list[str]]:
    """
    Heuristic bot score 0–100 + reasons.
    Higher = more likely automated.
    """
    score = 0
    reasons: list[str] = []

    ua = str(payload.get("ua") or ua_header or "").strip()
    lang = str(payload.get("lang") or "").strip()
    screen = str(payload.get("screen") or "").strip()
    path = str(payload.get("path") or "/")
    ref = str(payload.get("referrer") or payload.get("ref") or "").strip()
    tz = str(payload.get("tz") or "").strip()
    platform = str(payload.get("platform") or "").strip()
    hw = payload.get("hw")  # hardwareConcurrency
    mem = payload.get("mem")  # deviceMemory
    touch = payload.get("touch")
    webdriver = payload.get("webdriver")
    languages = payload.get("languages")
    dnt = payload.get("dnt")
    cookie = payload.get("cookie")
    client = str(payload.get("client") or "").strip()  # "lumen-web"

    # --- UA ---
    if not ua or ua == "-":
        score += 40
        reasons.append("empty_ua")
    elif _BOT_UA.search(ua):
        score += 50
        reasons.append("ua_pattern")
    if "HeadlessChrome" in ua or "Headless" in ua:
        score += 35
        reasons.append("headless")

    # --- Client must identify as lumen browser ping ---
    if client != "lumen-web":
        score += 25
        reasons.append("not_lumen_client")

    # --- webdriver / automation flag from browser ---
    if webdriver is True or webdriver == "1" or webdriver == 1:
        score += 45
        reasons.append("webdriver")

    # --- screen heuristics ---
    if screen in ("800x600", "0x0", "1x1", ""):
        score += 20
        reasons.append("odd_screen")
    elif screen == "800x600" and "Chrome" in ua:
        score += 10
        reasons.append("default_headless_screen")

    # --- language ---
    if not lang or lang == "-":
        score += 10
        reasons.append("no_lang")
    if isinstance(languages, list) and len(languages) == 0:
        score += 10
        reasons.append("empty_languages")

    # --- timezone ---
    if not tz:
        score += 8
        reasons.append("no_tz")

    # --- platform ---
    if not platform:
        score += 5
        reasons.append("no_platform")

    # --- hardware signals (real mobile/desktop usually have these) ---
    try:
        if hw is not None and int(hw) == 0:
            score += 15
            reasons.append("hw_zero")
    except Exception:
        pass
    try:
        if mem is not None and float(mem) == 0:
            score += 10
            reasons.append("mem_zero")
    except Exception:
        pass

    # --- cookies enabled (bots often disable) ---
    if cookie is False or cookie == "0" or cookie == 0:
        score += 12
        reasons.append("cookies_off")

    # --- IP datacenter heuristic (weak) ---
    if ip and any(ip.startswith(p) for p in _CLOUD_IP_PREFIXES):
        # only boost if other signals present
        if score >= 20:
            score += 10
            reasons.append("cloud_ip")

    # --- path only root with zero engagement signals ---
    if path in ("/", "/index.html") and not ref and score >= 15:
        score += 5
        reasons.append("root_no_ref")

    # Cap
    if score > 100:
        score = 100
    return score, reasons


def is_bot(payload: dict[str, Any], *, ip: str = "", ua_header: str = "") -> tuple[bool, int, list[str]]:
    sc, reasons = bot_score(payload, ip=ip, ua_header=ua_header)
    return sc >= BOT_SCORE_THRESHOLD, sc, reasons


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
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        "👁 Pengunjung baru\n————————————\n"
        f"Halaman: {path}\nIP: {ip}\nDari: {ref_line}\n"
        f"Bahasa: {lang}\nLayar: {screen}\n{short_ua}"
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
        {"phone": WHATSAPP_PHONE, "text": plain[:900], "apikey": WHATSAPP_APIKEY}
    )
    url = f"https://api.callmebot.com/whatsapp.php?{q}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "LumenVisitNotify/1"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def notify_visit(payload: dict[str, Any], *, ip: str = "unknown", ua_header: str = "") -> dict[str, Any]:
    if not enabled():
        return {"ok": False, "reason": "notify_disabled"}

    if not isinstance(payload, dict):
        payload = {}

    if SKIP_BOTS:
        bot, sc, reasons = is_bot(payload, ip=ip or "", ua_header=ua_header)
        if bot:
            return {
                "ok": True,
                "skipped": "bot",
                "score": sc,
                "reasons": reasons[:8],
            }

    if not _allow(ip or "unknown"):
        return {"ok": True, "skipped": "cooldown_or_rate"}

    path = str(payload.get("path") or "/")[:200]
    ref = str(payload.get("referrer") or payload.get("ref") or "-")[:200]
    ua = str(payload.get("ua") or ua_header or "-")[:200]
    lang = str(payload.get("lang") or "-")[:40]
    screen = str(payload.get("screen") or "-")[:40]

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
