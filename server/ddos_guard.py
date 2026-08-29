"""
DDoS / flood guard for Lumen.

- Per-IP sliding window rate tracking
- Temporary soft-ban after repeated limit hits
- Global RPS spike detection → Telegram alert (cooldown)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections import defaultdict, deque

# --- config (env override) ---
WINDOW = float(os.environ.get("DDOS_WINDOW_SEC", "60"))
# hard trip: this many requests / WINDOW → soft-ban
IP_HARD_LIMIT = int(os.environ.get("DDOS_IP_HARD", "90"))
# soft trip: warn after this many / WINDOW
IP_SOFT_LIMIT = int(os.environ.get("DDOS_IP_SOFT", "45"))
BAN_SECONDS = int(os.environ.get("DDOS_BAN_SEC", "300"))  # 5 min
# global: if total RPS over GLOBAL_RPS for GLOBAL_SPIKE_SEC → alert
GLOBAL_RPS = float(os.environ.get("DDOS_GLOBAL_RPS", "40"))
GLOBAL_SPIKE_SEC = float(os.environ.get("DDOS_SPIKE_SEC", "20"))
ALERT_COOLDOWN = int(os.environ.get("DDOS_ALERT_COOLDOWN", "300"))  # 5 min between similar alerts

TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

_lock = threading.Lock()
_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_ip_violations: dict[str, int] = defaultdict(int)
_bans: dict[str, float] = {}  # ip -> ban_until
_global_hits: deque[float] = deque()
_last_alert: dict[str, float] = {}
_started_prune = False


def _now() -> float:
    return time.time()


def _prune_deque(q: deque, cutoff: float) -> None:
    while q and q[0] < cutoff:
        q.popleft()


def _telegram(text: str) -> None:
    if not TOKEN or not CHAT_ID:
        print("[ddos] alert (no telegram cfg):", text[:200], flush=True)
        return

    def _send() -> None:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            body = json.dumps(
                {
                    "chat_id": CHAT_ID,
                    "text": text[:3500],
                    "disable_web_page_preview": True,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                resp.read()
        except Exception as e:
            print("[ddos] telegram send failed:", e, flush=True)

    threading.Thread(target=_send, daemon=True).start()


def _alert(kind: str, text: str) -> None:
    now = _now()
    with _lock:
        last = _last_alert.get(kind, 0)
        if now - last < ALERT_COOLDOWN:
            return
        _last_alert[kind] = now
    print("[ddos] ALERT", kind, text[:120], flush=True)
    _telegram(text)


def is_banned(ip: str) -> tuple[bool, int]:
    """Return (banned, retry_after_sec)."""
    if not ip:
        return False, 0
    now = _now()
    with _lock:
        until = _bans.get(ip)
        if until is None:
            return False, 0
        if until <= now:
            _bans.pop(ip, None)
            return False, 0
        return True, max(1, int(until - now))


def ban_ip(ip: str, seconds: int | None = None, reason: str = "") -> None:
    sec = seconds if seconds is not None else BAN_SECONDS
    until = _now() + sec
    with _lock:
        _bans[ip] = max(_bans.get(ip, 0), until)
    _alert(
        f"ban:{ip}",
        "🚨 Lumen soft-ban IP\n"
        f"IP: {ip}\n"
        f"Durasi: {sec}s\n"
        f"Alasan: {reason or 'rate / flood'}\n"
        f"Waktu: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
    )


def note_request(ip: str, path: str = "") -> tuple[bool, str, int]:
    """
    Record a request. Returns (allowed, reason, retry_after).
    reason: ok | banned | rate_ip | global_spike (still may allow with caution)
    """
    global _started_prune
    ip = (ip or "unknown").strip() or "unknown"
    now = _now()
    path = (path or "")[:120]

    # already banned?
    banned, retry = is_banned(ip)
    if banned:
        return False, "banned", retry

    with _lock:
        # per-IP window
        q = _ip_hits[ip]
        _prune_deque(q, now - WINDOW)
        q.append(now)
        ip_count = len(q)

        # global window (last GLOBAL_SPIKE_SEC)
        _prune_deque(_global_hits, now - max(WINDOW, GLOBAL_SPIKE_SEC))
        _global_hits.append(now)
        # RPS over last GLOBAL_SPIKE_SEC
        cutoff = now - GLOBAL_SPIKE_SEC
        global_n = sum(1 for t in _global_hits if t >= cutoff)
        rps = global_n / max(GLOBAL_SPIKE_SEC, 1.0)

        # periodic cleanup of ban map
        if not _started_prune:
            _started_prune = True

        if ip_count > IP_HARD_LIMIT:
            _ip_violations[ip] += 1
            # escalate ban duration on repeat
            mult = min(4, 1 + _ip_violations[ip] // 2)
            ban_until = now + BAN_SECONDS * mult
            _bans[ip] = ban_until
            retry_after = int(BAN_SECONDS * mult)
            # release lock before alert
            pass
        else:
            ban_until = None
            retry_after = 0

    if ip_count > IP_HARD_LIMIT:
        _alert(
            f"flood:{ip}",
            "🚨 Lumen indikasi DDoS / flood\n"
            f"IP: {ip}\n"
            f"Path: {path}\n"
            f"Hit ~{ip_count} / {int(WINDOW)}s (limit {IP_HARD_LIMIT})\n"
            f"Global ~{rps:.1f} req/s\n"
            f"Aksi: soft-ban {retry_after}s\n"
            f"Waktu: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        )
        return False, "rate_ip", retry_after

    if ip_count > IP_SOFT_LIMIT:
        # soft warning once per cooldown
        _alert(
            f"soft:{ip}",
            "⚠️ Lumen traffic tinggi per IP\n"
            f"IP: {ip}\n"
            f"Path: {path}\n"
            f"Hit ~{ip_count} / {int(WINDOW)}s (soft {IP_SOFT_LIMIT})\n"
            f"Belum di-ban — pantau.\n"
            f"Waktu: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        )

    if rps >= GLOBAL_RPS:
        _alert(
            "global_spike",
            "🚨 Lumen spike global (mungkin DDoS)\n"
            f"~{rps:.1f} req/s selama {int(GLOBAL_SPIKE_SEC)}s "
            f"(threshold {GLOBAL_RPS})\n"
            f"Contoh IP: {ip}\n"
            f"Path: {path}\n"
            f"Waktu: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        )

    return True, "ok", 0


def stats() -> dict:
    now = _now()
    with _lock:
        active_bans = {ip: int(u - now) for ip, u in _bans.items() if u > now}
        _prune_deque(_global_hits, now - WINDOW)
        return {
            "window_sec": WINDOW,
            "ip_soft_limit": IP_SOFT_LIMIT,
            "ip_hard_limit": IP_HARD_LIMIT,
            "ban_sec": BAN_SECONDS,
            "global_rps_threshold": GLOBAL_RPS,
            "tracked_ips": len(_ip_hits),
            "active_bans": active_bans,
            "hits_last_window": len(_global_hits),
            "telegram_configured": bool(TOKEN and CHAT_ID),
        }
