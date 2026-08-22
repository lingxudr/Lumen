const TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const CHAT = process.env.TELEGRAM_CHAT_ID || "";
const DISCORD = process.env.DISCORD_WEBHOOK_URL || "";
const WA_PHONE = (process.env.WHATSAPP_PHONE || "").replace(/^\+/, "");
const WA_KEY = process.env.WHATSAPP_APIKEY || process.env.CALLMEBOT_APIKEY || "";
const UPSTREAM = (process.env.LUMEN_UPSTREAM || "").replace(/\/$/, "");
const SKIP_BOTS = (process.env.VISIT_NOTIFY_SKIP_BOTS || "1") !== "0";
const THRESHOLD = parseInt(process.env.VISIT_BOT_SCORE_THRESHOLD || "55", 10);

const BOT_UA =
  /bot|crawl|spider|slurp|headless|phantom|selenium|puppeteer|playwright|lighthouse|pagespeed|pingdom|uptimerobot|statuscake|gtmetrix|preview|facebookexternalhit|facebot|twitterbot|linkedinbot|pinterest|whatsapp|telegram|discord|slackbot|vercel-screenshot|chrome-lighthouse|googlebot|bingbot|yandex|baidu|semrush|ahrefs|mj12bot|dotbot|petalbot|bytespider|python-requests|curl\/|wget\/|httpclient|go-http|okhttp|scrapy|aiohttp|httpx|monitor|checker|scan|archive\.org/i;

function clientIp(req) {
  const xf = req.headers["x-forwarded-for"];
  if (typeof xf === "string" && xf.length) return xf.split(",")[0].trim();
  return req.headers["x-real-ip"] || "unknown";
}

function botScore(data, ip, uaHeader) {
  let score = 0;
  const reasons = [];
  const ua = String(data.ua || uaHeader || "").trim();
  const lang = String(data.lang || "").trim();
  const screen = String(data.screen || "").trim();
  const path = String(data.path || "/");
  const ref = String(data.referrer || data.ref || "").trim();
  const tz = String(data.tz || "").trim();
  const platform = String(data.platform || "").trim();
  const client = String(data.client || "").trim();

  if (!ua || ua === "-") {
    score += 40;
    reasons.push("empty_ua");
  } else if (BOT_UA.test(ua)) {
    score += 50;
    reasons.push("ua_pattern");
  }
  if (/Headless/i.test(ua)) {
    score += 35;
    reasons.push("headless");
  }
  if (client !== "lumen-web") {
    score += 25;
    reasons.push("not_lumen_client");
  }
  if (data.webdriver === true || data.webdriver === 1 || data.webdriver === "1") {
    score += 45;
    reasons.push("webdriver");
  }
  if (["800x600", "0x0", "1x1", ""].includes(screen)) {
    score += 20;
    reasons.push("odd_screen");
  }
  if (!lang || lang === "-") {
    score += 10;
    reasons.push("no_lang");
  }
  if (Array.isArray(data.languages) && data.languages.length === 0) {
    score += 10;
    reasons.push("empty_languages");
  }
  if (!tz) {
    score += 8;
    reasons.push("no_tz");
  }
  if (!platform) {
    score += 5;
    reasons.push("no_platform");
  }
  if (data.hw === 0 || data.hw === "0") {
    score += 15;
    reasons.push("hw_zero");
  }
  if (data.cookie === false || data.cookie === 0 || data.cookie === "0") {
    score += 12;
    reasons.push("cookies_off");
  }
  if (path === "/" && !ref && score >= 15) {
    score += 5;
    reasons.push("root_no_ref");
  }
  return { score: Math.min(100, score), reasons };
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatHtml(path, ip, ref, lang, screen, ua) {
  const shortUa = ua.length > 80 ? ua.slice(0, 77) + "…" : ua;
  const refLine = ref && ref !== "-" ? ref : "langsung";
  return (
    "👁 <b>Pengunjung baru</b>\n━━━━━━━━━━━━\n" +
    `📄 <b>Halaman</b>\n<code>${esc(path)}</code>\n\n` +
    `🌐 <b>IP</b>  <code>${esc(ip)}</code>\n` +
    `🔗 <b>Dari</b>  ${esc(refLine)}\n` +
    `🗣 <b>Bahasa</b>  ${esc(lang)}\n` +
    `📱 <b>Layar</b>  ${esc(screen)}\n\n` +
    `<i>${esc(shortUa)}</i>`
  );
}

function formatPlain(path, ip, ref, lang, screen, ua) {
  const shortUa = ua.length > 80 ? ua.slice(0, 77) + "…" : ua;
  const refLine = ref && ref !== "-" ? ref : "langsung";
  return (
    "👁 Pengunjung baru\n————————————\n" +
    `Halaman: ${path}\nIP: ${ip}\nDari: ${refLine}\n` +
    `Bahasa: ${lang}\nLayar: ${screen}\n${shortUa}`
  );
}

async function sendTelegram(html, plain) {
  if (!TOKEN || !CHAT) return;
  const url = `https://api.telegram.org/bot${TOKEN}/sendMessage`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: CHAT,
      text: html,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  if (!r.ok) {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: CHAT,
        text: plain,
        disable_web_page_preview: true,
      }),
    });
  }
}

async function sendDiscord(plain) {
  if (!DISCORD) return;
  await fetch(DISCORD, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: String(plain).slice(0, 1900) }),
  });
}

async function sendWhatsApp(plain) {
  if (!WA_PHONE || !WA_KEY) return;
  const q = new URLSearchParams({
    phone: WA_PHONE,
    text: String(plain).slice(0, 900),
    apikey: WA_KEY,
  });
  await fetch(`https://api.callmebot.com/whatsapp.php?${q}`, {
    headers: { "User-Agent": "LumenVisitNotify/1" },
  });
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  const any = (TOKEN && CHAT) || DISCORD || (WA_PHONE && WA_KEY) || UPSTREAM;
  if (!any) return res.status(200).json({ ok: false, reason: "notify_disabled" });

  let data = {};
  try {
    if (req.method === "POST" && req.body) {
      data = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
    } else if (req.query) data = req.query;
  } catch (_) {
    data = {};
  }

  const ip = clientIp(req);
  const uaHeader = req.headers["user-agent"] || "";
  if (SKIP_BOTS) {
    const { score, reasons } = botScore(data, ip, uaHeader);
    if (score >= THRESHOLD) {
      return res.status(200).json({ ok: true, skipped: "bot", score, reasons: reasons.slice(0, 8) });
    }
  }

  const path = String(data.path || "/").slice(0, 200);
  const ref = String(data.referrer || data.ref || "-").slice(0, 200);
  const ua = String(data.ua || uaHeader || "-").slice(0, 200);
  const lang = String(data.lang || "-").slice(0, 40);
  const screen = String(data.screen || "-").slice(0, 40);
  const html = formatHtml(path, ip, ref, lang, screen, ua);
  const plain = formatPlain(path, ip, ref, lang, screen, ua);

  try {
    await Promise.allSettled([
      sendTelegram(html, plain),
      sendDiscord(plain),
      sendWhatsApp(plain),
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
