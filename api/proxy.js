const API_BASE = "https://be.komikcast.cc";
const UA =
  "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36";

const CACHE = new Map();
const TTL_MS = 45_000;

function cacheGet(key) {
  const row = CACHE.get(key);
  if (!row) return null;
  if (Date.now() > row.exp) {
    CACHE.delete(key);
    return null;
  }
  return row.body;
}

function cacheSet(key, body) {
  if (CACHE.size > 80) {
    const first = CACHE.keys().next().value;
    CACHE.delete(first);
  }
  CACHE.set(key, { body, exp: Date.now() + TTL_MS });
}

function resolvePath(req) {
  // 1) query.path from rewrite /api/proxy?path=series/...
  let p = req.query && req.query.path;
  if (Array.isArray(p)) p = p.join("/");
  if (typeof p === "string" && p.length) {
    return p.replace(/^\/+/, "").replace(/\.\./g, "");
  }

  // 2) parse from URL: /api/series, /api/proxy, /api/series/foo/chapters/1
  try {
    const host = req.headers?.host || "localhost";
    const u = new URL(req.url || "/", `https://${host}`);
    let pathname = u.pathname || "";
    // strip /api/proxy or /api/
    pathname = pathname.replace(/^\/api\/proxy\/?/, "");
    pathname = pathname.replace(/^\/api\//, "");
    pathname = pathname.replace(/^\/+/, "");
    if (pathname && pathname !== "proxy") {
      return pathname.replace(/\.\./g, "");
    }
  } catch (_) {}

  return "";
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  try {
    const subPath = resolvePath(req);
    if (!subPath) {
      return res.status(400).json({
        error: "missing path",
        hint: "Use /api/series or /api/proxy?path=series",
        url: req.url || null,
        query: req.query || null,
      });
    }

    const qs = new URLSearchParams();
    Object.keys(req.query || {}).forEach((k) => {
      if (k === "path") return;
      const v = req.query[k];
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else if (v != null && v !== "") qs.set(k, String(v));
    });
    // also parse search from raw url in case query is incomplete
    try {
      const host = req.headers?.host || "localhost";
      const u = new URL(req.url || "/", `https://${host}`);
      u.searchParams.forEach((v, k) => {
        if (k === "path") return;
        if (!qs.has(k)) qs.set(k, v);
      });
    } catch (_) {}

    const q = qs.toString();
    const target = `${API_BASE}/${subPath}${q ? `?${q}` : ""}`;

    const hit = cacheGet(target);
    if (hit) {
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("X-Lumen-Cache", "HIT");
      res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=120");
      return res.status(200).send(hit);
    }

    const upstream = await fetch(target, {
      headers: {
        "User-Agent": UA,
        Accept: "application/json, text/plain, */*",
        Origin: "https://v3.komikcast.fit",
        Referer: "https://v3.komikcast.fit/",
      },
    });
    const ct = upstream.headers.get("content-type") || "application/json; charset=utf-8";
    const buf = Buffer.from(await upstream.arrayBuffer());
    if (upstream.status === 200) cacheSet(target, buf);
    res.setHeader("Content-Type", ct);
    res.setHeader("X-Lumen-Cache", "MISS");
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=120");
    return res.status(upstream.status).send(buf);
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
