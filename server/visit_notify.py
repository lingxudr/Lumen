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



def device_info(ua: str, screen: str = "", platform: str = "") -> dict[str, str]:
    """Best-effort device/OS/browser from User-Agent (no exact iPhone model — Apple hides it)."""
    u = ua or ""
    device = "Unknown"
    os_name = "Unknown"
    browser = "Unknown"

    # --- OS + device class ---
    m = re.search(r"iPhone OS ([0-9_]+)", u) or re.search(r"CPU OS ([0-9_]+)", u)
    if "iPhone" in u:
        device = "iPhone"
        if m:
            os_name = "iOS " + m.group(1).replace("_", ".")
        else:
            os_name = "iOS"
    elif "iPad" in u:
        device = "iPad"
        if m:
            os_name = "iPadOS " + m.group(1).replace("_", ".")
        else:
            os_name = "iPadOS"
    elif "Android" in u:
        device = "Android"
        am = re.search(r"Android ([0-9.]+)", u)
        os_name = f"Android {am.group(1)}" if am else "Android"
        # model in parentheses: Linux; Android 13; SM-S918B
        mm = re.search(r"Android [^;]+;\s*([^)]+?)\s*Build", u) or re.search(
            r"Android [^;]+;\s*([^);]+)", u
        )
        if mm:
            model = mm.group(1).strip()
            if model and model.lower() not in ("wv", "mobile", "u"):
                device = f"Android ({model[:40]})"
    elif "Windows" in u:
        device = "PC"
        wm = re.search(r"Windows NT ([0-9.]+)", u)
        win_map = {"10.0": "10/11", "6.3": "8.1", "6.1": "7"}
        ver = wm.group(1) if wm else ""
        os_name = "Windows " + win_map.get(ver, ver or "")
    elif "Mac OS X" in u or "Macintosh" in u:
        device = "Mac"
        mm = re.search(r"Mac OS X ([0-9_]+)", u)
        os_name = "macOS " + mm.group(1).replace("_", ".") if mm else "macOS"
    elif "Linux" in u:
        device = "Linux PC"
        os_name = "Linux"
    elif platform:
        device = platform[:40]

    # --- Browser ---
    if "Edg/" in u or "Edge/" in u:
        bm = re.search(r"Edg[e]?/([0-9.]+)", u)
        browser = "Edge " + (bm.group(1).split(".")[0] if bm else "")
    elif "OPR/" in u or "Opera" in u:
        bm = re.search(r"OPR/([0-9.]+)", u)
        browser = "Opera " + (bm.group(1).split(".")[0] if bm else "")
    elif "SamsungBrowser/" in u:
        bm = re.search(r"SamsungBrowser/([0-9.]+)", u)
        browser = "Samsung Internet " + (bm.group(1).split(".")[0] if bm else "")
    elif "Chrome/" in u and "Chromium" not in u and "Edg" not in u:
        bm = re.search(r"Chrome/([0-9.]+)", u)
        browser = "Chrome " + (bm.group(1).split(".")[0] if bm else "")
    elif "Firefox/" in u:
        bm = re.search(r"Firefox/([0-9.]+)", u)
        browser = "Firefox " + (bm.group(1).split(".")[0] if bm else "")
    elif "Safari/" in u and "Chrome" not in u:
        bm = re.search(r"Version/([0-9.]+)", u)
        browser = "Safari " + (bm.group(1).split(".")[0] if bm else "")
    elif "AppleWebKit" in u and ("iPhone" in u or "iPad" in u):
        browser = "Safari"

    return {
        "device": device.strip(),
        "os": os_name.strip(),
        "browser": browser.strip(),
        "screen": screen or "-",
    }


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


def _format_html(
    path: str, ip: str, ref: str, lang: str, screen: str, ua: str, platform: str = ""
) -> str:
    short_ua = ua if len(ua) <= 72 else ua[:69] + "…"
    ref_line = ref if ref and ref != "-" else "langsung"
    d = device_info(ua, screen, platform)
    return (
        "👁 <b>Pengunjung baru</b>\n"
        "━━━━━━━━━━━━\n"
        f"📄 <b>Halaman</b>\n<code>{_esc(path)}</code>\n\n"
        f"📱 <b>Perangkat</b>  {_esc(d['device'])}\n"
        f"💻 <b>OS</b>  {_esc(d['os'])}\n"
        f"🌐 <b>Browser</b>  {_esc(d['browser'])}\n"
        f"📐 <b>Layar</b>  {_esc(d['screen'])}\n\n"
        f"🌍 <b>IP</b>  <code>{_esc(ip)}</code>\n"
        f"🔗 <b>Dari</b>  {_esc(ref_line)}\n"
        f"🗣 <b>Bahasa</b>  {_esc(lang)}\n\n"
        f"<i>{_esc(short_ua)}</i>"
    )


def _format_plain(
    path: str, ip: str, ref: str, lang: str, screen: str, ua: str, platform: str = ""
) -> str:
    short_ua = ua if len(ua) <= 72 else ua[:69] + "…"
    ref_line = ref if ref and ref != "-" else "langsung"
    d = device_info(ua, screen, platform)
    return (
        "👁 Pengunjung baru\n————————————\n"
        f"Halaman: {path}\n"
        f"Perangkat: {d['device']}\n"
        f"OS: {d['os']}\n"
        f"Browser: {d['browser']}\n"
        f"Layar: {d['screen']}\n"
        f"IP: {ip}\nDari: {ref_line}\nBahasa: {lang}\n"
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

    platform = str(payload.get("platform") or "")[:40]
    html = _format_html(path, ip, ref, lang, screen, ua, platform)
    plain = _format_plain(path, ip, ref, lang, screen, ua, platform)

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
