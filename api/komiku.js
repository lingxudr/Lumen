/**
 * Vercel serverless proxy → Komiku (bypass Railway IP ban).
 * Usage: /api/komiku/wp-json/wp/v2/manga?orderby=modified
 *        /api/komiku/?path=/manga/slug/
 *
 * Only allows komiku.org / komiku.id hosts.
 */
const ALLOWED = new Set(["komiku.org", "www.komiku.org", "komiku.id", "www.komiku.id", "api.komiku.org"]);

function pickHost(req) {
  const h = (req.query && req.query.host) || "komiku.org";
  const host = String(h).replace(/^https?:\/\//, "").split("/")[0];
  return ALLOWED.has(host) ? host : "komiku.org";
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method !== "GET") {
    res.status(405).json({ error: "method_not_allowed" });
    return;
  }

  try {
    const host = pickHost(req);
    // path after /api/komiku
    let sub = "";
    if (req.query && req.query.path) {
      sub = String(req.query.path);
    } else if (req.url) {
      // /api/komiku/wp-json/... or rewrite
      const u = req.url.split("?")[0];
      const idx = u.indexOf("/api/komiku");
      sub = idx >= 0 ? u.slice(idx + "/api/komiku".length) : u;
    }
    if (!sub.startsWith("/")) sub = "/" + sub;
    if (sub === "/") sub = "/";

    // rebuild query without host/path control keys
    const q = new URLSearchParams();
    if (req.query) {
      for (const [k, v] of Object.entries(req.query)) {
        if (k === "host" || k === "path") continue;
        if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
        else if (v != null) q.append(k, String(v));
      }
    }
    const qs = q.toString();
    const target = `https://${host}${sub}${qs ? "?" + qs : ""}`;

    const r = await fetch(target, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        Accept: "application/json, text/html, */*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        Referer: `https://${host}/`,
      },
      redirect: "follow",
    });

    const ct = r.headers.get("content-type") || "application/octet-stream";
    const buf = Buffer.from(await r.arrayBuffer());
    res.status(r.status);
    res.setHeader("Content-Type", ct);
    res.setHeader("Cache-Control", "public, max-age=60, s-maxage=120");
    res.setHeader("X-Komiku-Proxy", target);
    res.send(buf);
  } catch (e) {
    res.status(502).json({ error: "proxy_failed", message: String(e && e.message ? e.message : e) });
  }
};
