const API_BASE = "https://be.komikcast.cc";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";

function resolvePath(reqUrl, query) {
  let p = query && query.path;
  if (Array.isArray(p)) p = p.join("/");
  if (typeof p === "string" && p.length) {
    return decodeURIComponent(p).replace(/^\/+/, "").replace(/\.\./g, "");
  }
  try {
    const u = new URL(reqUrl, "https://localhost");
    let pathname = u.pathname || "";
    pathname = pathname.replace(/^\/api\/proxy\/?/, "").replace(/^\/api\//, "").replace(/^\/+/, "");
    if (pathname && pathname !== "proxy") return pathname.replace(/\.\./g, "");
  } catch (_) {}
  return "";
}

function collectQuery(query, reqUrl) {
  const qs = new URLSearchParams();
  if (query) {
    Object.keys(query).forEach((k) => {
      if (k === "path") return;
      const v = query[k];
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else if (v != null && v !== "") qs.set(k, String(v));
    });
  }
  try {
    const u = new URL(reqUrl, "https://localhost");
    u.searchParams.forEach((v, k) => {
      if (k === "path") return;
      if (!qs.has(k)) qs.set(k, v);
    });
  } catch (_) {}
  return qs;
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  try {
    const subPath = resolvePath(req.url || "/", req.query || {});
    if (!subPath) {
      return res.status(400).json({ error: "missing path", url: req.url || null });
    }

    const qs = collectQuery(req.query, req.url || "/");
    const q = qs.toString();
    const target = `${API_BASE}/${subPath}${q ? `?${q}` : ""}`;

    const upstream = await fetch(target, {
      headers: {
        "User-Agent": UA,
        Accept: "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        Origin: "https://v3.komikcast.fit",
        Referer: "https://v3.komikcast.fit/",
      },
      redirect: "follow",
    });

    const ct = upstream.headers.get("content-type") || "";
    const buf = Buffer.from(await upstream.arrayBuffer());
    const textStart = buf.subarray(0, 80).toString("utf8").toLowerCase();

    // Cloudflare / WAF challenge HTML
    if (
      ct.includes("text/html") ||
      textStart.includes("<!doctype") ||
      textStart.includes("just a moment")
    ) {
      return res.status(503).json({
        error: "upstream_blocked",
        message:
          "Server sumber memblokir IP Vercel (Cloudflare). Deploy proxy di Railway/Render, atau pakai server Python lokal.",
        status: upstream.status,
      });
    }

    res.setHeader("Content-Type", ct || "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=120");
    return res.status(upstream.status).send(buf);
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
