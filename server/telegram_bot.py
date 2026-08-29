"""Lightweight Telegram long-poll for /online /start commands."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request

TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ALLOW = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()  # only this chat

_offset = 0
_started = False


def _api(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _send(chat_id: str | int, text: str) -> None:
    _api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text[:3500],
            "disable_web_page_preview": True,
        },
    )


def _handle_message(msg: dict) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = (msg.get("text") or "").strip()
    if not text:
        return
    # Restrict to owner chat if configured
    if CHAT_ALLOW and chat_id != str(CHAT_ALLOW):
        return
    cmd = text.split()[0].split("@")[0].lower()
    if cmd in ("/online", "/aktif", "/who"):
        try:
            from presence import summary_text
        except Exception:
            from server.presence import summary_text  # type: ignore
        _send(chat_id, summary_text())
    elif cmd in ("/ddos", "/ban", "/guard"):
        try:
            import ddos_guard
            st = ddos_guard.stats()
            bans = st.get("active_bans") or {}
            lines = [
                "🛡 Lumen DDoS guard",
                f"Telegram: {'✅' if st.get('telegram_configured') else '❌ set TELEGRAM_*'}",
                f"Soft limit: {st.get('ip_soft_limit')}/min",
                f"Hard limit: {st.get('ip_hard_limit')}/min → ban {st.get('ban_sec')}s",
                f"Global RPS alert: {st.get('global_rps_threshold')}",
                f"Tracked IPs: {st.get('tracked_ips')}",
                f"Active bans: {len(bans)}",
            ]
            for ip, sec in list(bans.items())[:10]:
                lines.append(f"  • {ip} ({sec}s left)")
            _send(chat_id, "\n".join(lines))
        except Exception as e:
            _send(chat_id, f"ddos stats error: {e}")
    elif cmd in ("/start", "/help"):
        _send(
            chat_id,
            "Lumen presence bot\n"
            "/online — pengunjung aktif sekarang\n"
            "/ddos — status proteksi flood\n"
            "/help — bantuan",
        )


def _loop() -> None:
    global _offset
    if not TOKEN:
        return
    print("[telegram_bot] poller start", flush=True)
    while True:
        try:
            res = _api(
                "getUpdates",
                {"timeout": 25, "offset": _offset, "allowed_updates": ["message"]},
            )
            if not res.get("ok"):
                time.sleep(5)
                continue
            for upd in res.get("result") or []:
                _offset = int(upd.get("update_id", 0)) + 1
                msg = upd.get("message") or upd.get("edited_message")
                if msg:
                    try:
                        _handle_message(msg)
                    except Exception as e:
                        print("[telegram_bot] handle:", e, flush=True)
        except Exception as e:
            print("[telegram_bot] poll:", e, flush=True)
            time.sleep(8)


def start_background() -> None:
    global _started
    if _started or not TOKEN:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="telegram-poller").start()
