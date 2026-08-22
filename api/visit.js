/**
 * Visitor notify — WhatsApp (CallMeBot) / Telegram / Discord
 * Env:
 *   WHATSAPP_PHONE=62812xxxxxxxx  (kode negara, tanpa +)
 *   WHATSAPP_APIKEY=...           (dari CallMeBot)
 *   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
 *   DISCORD_WEBHOOK_URL
 */
const TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const CHAT = process.env.TELEGRAM_CHAT_ID || "";
const DISCORD = process.env.DISCORD_WEBHOOK_URL || "";
const WA_PHONE = (process.env.WHATSAPP_PHONE || "").replace(/^\+/, "");
const WA_KEY = process.env.WHATSAPP_APIKEY || process.env.CALLMEBOT_APIKEY || "";
const UPSTREAM = (process.env.LUMEN_UPSTREAM || "").replace(/\/$/, "");

function clientIp(req) {
  const xf = req.headers["x-forwarded-for"];
  if (typeof xf === "string" && xf.length) return xf.split(",")[0].trim();
  return req.headers["x-real-ip"] || "unknown";
}

async function sendTelegram(text) {
  if (!TOKEN || !CHAT) return;
  await fetch(`https://api.telegram.org/bot${TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: CHAT, text, disable_web_page_preview: true }),
  });
}

async function sendDiscord(text) {
  if (!DISCORD) return;
  await fetch(DISCORD, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: String(text).slice(0, 1900) }),
  });
}

async function sendWhatsApp(text) {
  if (!WA_PHONE || !WA_KEY) return;
  const q = new URLSearchParams({
    phone: WA_PHONE,
    text: String(text).slice(0, 900),
    apikey: WA_KEY,
  });
  await fetch(`https://api.callmebot.com/whatsapp.php?${q.toString()}`, {
    headers: { "User-Agent": "LumenVisitNotify/1" },
  });
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  const any =
    (TOKEN && CHAT) || DISCORD || (WA_PHONE && WA_KEY) || UPSTREAM;
  if (!any) {
    return res.status(200).json({ ok: false, reason: "notify_disabled" });
  }

  let data = {};
  try {
    if (req.method === "POST" && req.body) {
      data = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
    } else if (req.query) {
      data = req.query;
    }
  } catch (_) {
    data = {};
  }

  const ip = clientIp(req);
  const path = String(data.path || "/").slice(0, 200);
  const ref = String(data.referrer || data.ref || "-").slice(0, 200);
  const ua = String(data.ua || req.headers["user-agent"] || "-").slice(0, 120);
  const lang = String(data.lang || "-").slice(0, 40);
  const screen = String(data.screen || "-").slice(0, 40);

  const text = [
    "Pengunjung Lumen",
    `Path: ${path}`,
    `IP: ${ip}`,
    `Ref: ${ref}`,
    `Lang: ${lang}`,
    `Screen: ${screen}`,
    `UA: ${ua}`,
  ].join("\n");

  try {
    await Promise.allSettled([
      sendWhatsApp(text),
      sendTelegram(text),
      sendDiscord(text),
    ]);
  } catch (_) {}

  if (UPSTREAM) {
    try {
      await fetch(`${UPSTREAM}/api/visit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (_) {}
  }

  return res.status(200).json({ ok: true, queued: true });
};
