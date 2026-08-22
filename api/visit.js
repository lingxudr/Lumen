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



async function lookupGeo(ip) {
  const empty = { country: "", countryCode: "", city: "", region: "", isp: "" };
  if (!ip || ip === "unknown" || ip.startsWith("10.") || ip.startsWith("192.168.") || ip === "127.0.0.1") {
    return empty;
  }
  try {
    const url =
      "http://ip-api.com/json/" +
      encodeURIComponent(ip) +
      "?fields=status,country,countryCode,regionName,city,isp,query";
    const r = await fetch(url, { headers: { "User-Agent": "LumenGeo/1" } });
    const raw = await r.json();
    if (raw && raw.status === "success") {
      return {
        country: String(raw.country || "").slice(0, 60),
        countryCode: String(raw.countryCode || "").slice(0, 8),
        city: String(raw.city || "").slice(0, 60),
        region: String(raw.regionName || "").slice(0, 60),
        isp: String(raw.isp || "").slice(0, 80),
      };
    }
  } catch (_) {}
  return empty;
}

function flagEmoji(cc) {
  const c = String(cc || "").toUpperCase();
  if (c.length !== 2) return "";
  return String.fromCodePoint(...[...c].map((ch) => 0x1f1e6 - 65 + ch.charCodeAt(0)));
}

function deviceInfo(ua, screen, platform) {
  const u = ua || "";
  let device = "Unknown";
  let os = "Unknown";
  let browser = "Unknown";
  let m;
  if (/iPhone/.test(u)) {
    device = "iPhone";
    m = u.match(/iPhone OS ([0-9_]+)/) || u.match(/CPU OS ([0-9_]+)/);
    os = m ? "iOS " + m[1].replace(/_/g, ".") : "iOS";
  } else if (/iPad/.test(u)) {
    device = "iPad";
    m = u.match(/CPU OS ([0-9_]+)/);
    os = m ? "iPadOS " + m[1].replace(/_/g, ".") : "iPadOS";
  } else if (/Android/.test(u)) {
    device = "Android";
    m = u.match(/Android ([0-9.]+)/);
    os = m ? "Android " + m[1] : "Android";
    const mm = u.match(/Android [^;]+;\s*([^)]+?)\s*Build/) || u.match(/Android [^;]+;\s*([^);]+)/);
    if (mm && mm[1] && !/^(wv|Mobile|U)$/i.test(mm[1].trim())) {
      device = "Android (" + mm[1].trim().slice(0, 40) + ")";
    }
  } else if (/Windows/.test(u)) {
    device = "PC";
    m = u.match(/Windows NT ([0-9.]+)/);
    const map = { "10.0": "10/11", "6.3": "8.1", "6.1": "7" };
    os = "Windows " + (m ? map[m[1]] || m[1] : "");
  } else if (/Mac OS X|Macintosh/.test(u)) {
    device = "Mac";
    m = u.match(/Mac OS X ([0-9_]+)/);
    os = m ? "macOS " + m[1].replace(/_/g, ".") : "macOS";
  } else if (/Linux/.test(u)) {
    device = "Linux PC";
    os = "Linux";
  } else if (platform) device = String(platform).slice(0, 40);

  if (/Edg\//.test(u) || /Edge\//.test(u)) {
    m = u.match(/Edg[e]?\/([0-9.]+)/);
    browser = "Edge " + (m ? m[1].split(".")[0] : "");
  } else if (/OPR\//.test(u)) {
    m = u.match(/OPR\/([0-9.]+)/);
    browser = "Opera " + (m ? m[1].split(".")[0] : "");
  } else if (/SamsungBrowser\//.test(u)) {
    m = u.match(/SamsungBrowser\/([0-9.]+)/);
    browser = "Samsung Internet " + (m ? m[1].split(".")[0] : "");
  } else if (/Chrome\//.test(u) && !/Edg/.test(u)) {
    m = u.match(/Chrome\/([0-9.]+)/);
    browser = "Chrome " + (m ? m[1].split(".")[0] : "");
  } else if (/Firefox\//.test(u)) {
    m = u.match(/Firefox\/([0-9.]+)/);
    browser = "Firefox " + (m ? m[1].split(".")[0] : "");
  } else if (/Safari\//.test(u) && !/Chrome/.test(u)) {
    m = u.match(/Version\/([0-9.]+)/);
    browser = "Safari " + (m ? m[1].split(".")[0] : "");
  } else if (/AppleWebKit/.test(u) && /iPhone|iPad/.test(u)) {
    browser = "Safari";
  }
  return { device, os, browser, screen: screen || "-" };
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatHtml(path, ip, ref, lang, screen, ua, platform, geo) {
  const shortUa = ua.length > 72 ? ua.slice(0, 69) + "…" : ua;
  const refLine = ref && ref !== "-" ? ref : "langsung";
  const d = deviceInfo(ua, screen, platform || "");
  const g = geo || {};
  const locParts = [g.city, g.region, g.country].filter(Boolean);
  const loc = locParts.join(", ");
  const flag = flagEmoji(g.countryCode);
  let msg =
    "👁 <b>Pengunjung baru</b>\n━━━━━━━━━━━━\n" +
    `📄 <b>Halaman</b>\n<code>${esc(path)}</code>\n\n` +
    `📱 <b>Perangkat</b>  ${esc(d.device)}\n` +
    `💻 <b>OS</b>  ${esc(d.os)}\n` +
    `🌐 <b>Browser</b>  ${esc(d.browser)}\n` +
    `📐 <b>Layar</b>  ${esc(d.screen)}\n\n` +
    `🌍 <b>IP</b>  <code>${esc(ip)}</code>\n`;
  if (loc) msg += `📍 <b>Lokasi</b>  ${esc((flag + " " + loc).trim())}\n`;
  if (g.isp) msg += `📡 <b>ISP</b>  ${esc(g.isp)}\n`;
  msg +=
    `🔗 <b>Dari</b>  ${esc(refLine)}\n` +
    `🗣 <b>Bahasa</b>  ${esc(lang)}\n\n` +
    `<i>${esc(shortUa)}</i>`;
  return msg;
}

function formatPlain(path, ip, ref, lang, screen, ua, platform, geo) {
  const shortUa = ua.length > 72 ? ua.slice(0, 69) + "…" : ua;
  const refLine = ref && ref !== "-" ? ref : "langsung";
  const d = deviceInfo(ua, screen, platform || "");
  const g = geo || {};
  const loc = [g.city, g.region, g.country].filter(Boolean).join(", ") || "-";
  return (
    "👁 Pengunjung baru\n————————————\n" +
    `Halaman: ${path}\nPerangkat: ${d.device}\nOS: ${d.os}\n` +
    `Browser: ${d.browser}\nLayar: ${d.screen}\n` +
    `IP: ${ip}\nLokasi: ${loc}\nISP: ${g.isp || "-"}\n` +
    `Dari: ${refLine}\nBahasa: ${lang}\n${shortUa}`
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
  const platform = String(data.platform || "").slice(0, 40);
  const geo = await lookupGeo(ip);
  const html = formatHtml(path, ip, ref, lang, screen, ua, platform, geo);
  const plain = formatPlain(path, ip, ref, lang, screen, ua, platform, geo);

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
