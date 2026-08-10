const API_BASE = "https://be.komikcast.cc";
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

const UA =
  "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36";

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  try {
    // req.query.path is array for catch-all
    let parts = req.query.path;
    if (!parts) return res.status(400).json({ error: "missing path" });
    if (!Array.isArray(parts)) parts = [parts];
    const sub = parts.map(encodeURIComponent).join("/");
    // decode then rejoin properly - path segments shouldn't all be encoded as full
    const subPath = parts.join("/");

    if (subPath.includes("..")) return res.status(400).json({ error: "bad path" });

    // forward other query params except path
    const qs = new URLSearchParams();
    Object.keys(req.query || {}).forEach((k) => {
      if (k === "path") return;
      const v = req.query[k];
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else if (v != null) qs.set(k, v);
    });
    const q = qs.toString();
    const target = `${API_BASE}/${subPath}${q ? `?${q}` : ""}`;

    const hit = cacheGet(target);
    if (hit) {
      res.setHeader("Content-Type", "application/json");
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
    const ct = upstream.headers.get("content-type") || "application/json";
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
