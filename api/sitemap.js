/**
 * Dedicated sitemap proxy — longer timeout, XML accept, cold-start retries.
 * Used by /sitemap.xml and child sitemaps via vercel rewrites.
 */
const API_BASE = (
  process.env.LUMEN_UPSTREAM || "https://lumen-production-d82a.up.railway.app"
).replace(/\/$/, "");

const MAP = {
  "": "sitemap",
  index: "sitemap",
  pages: "sitemap/pages",
  manga: "sitemap/manga",
  images: "sitemap/images",
};

async function fetchXml(path, attempts = 3) {
  let lastErr = null;
  for (let i = 0; i < attempts; i++) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 25000);
      const res = await fetch(`${API_BASE}/api/${path}`, {
        signal: ctrl.signal,
        headers: {
          "User-Agent": "LumenSitemapBot/1.0",
          Accept: "application/xml, text/xml, */*",
        },
        cache: "no-store",
      });
      clearTimeout(t);
      const buf = Buffer.from(await res.arrayBuffer());
      const ct = res.headers.get("content-type") || "";
      const text = buf.toString("utf8", 0, 80);
      if (res.ok && (ct.includes("xml") || text.includes("<?xml"))) {
        return { status: 200, body: buf };
      }
      lastErr = `upstream ${res.status}`;
    } catch (e) {
      lastErr = e && e.message ? e.message : String(e);
      // brief backoff for Railway cold start
      await new Promise((r) => setTimeout(r, 800 * (i + 1)));
    }
  }
  return { status: 503, body: Buffer.from(`<!-- sitemap unavailable: ${lastErr} -->`) };
}

module.exports = async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    return res.end();
  }

  // path from query ?kind=manga|pages|images or default index
  const q = req.query || {};
  let kind = (q.kind || q.type || "").toString().toLowerCase();
  if (!kind && req.url) {
    try {
      const u = new URL(req.url, "http://localhost");
      kind = (u.searchParams.get("kind") || "").toLowerCase();
    } catch (_) {}
  }
  const apiPath = MAP[kind] || MAP[""];

  const { status, body } = await fetchXml(apiPath);
  res.setHeader("Content-Type", "application/xml; charset=utf-8");
  res.setHeader(
    "Cache-Control",
    status === 200
      ? "public, max-age=1800, s-maxage=3600, stale-while-revalidate=86400"
      : "public, max-age=60"
  );
  res.setHeader("X-Robots-Tag", "noindex"); // sitemap itself need not rank
  res.statusCode = status;
  res.end(body);
};
