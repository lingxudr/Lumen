/**
 * Edge proxy → Lumen Railway / Voratoon upstream.
 * Anti-SSRF: path relative saja, tidak boleh URL absolut / host asing.
 */
const API_BASE = process.env.LUMEN_UPSTREAM || "https://web-production-7769e.up.railway.app";
const ALLOWED_HOST = process.env.LUMEN_UPSTREAM_HOST || "web-production-7769e.up.railway.app";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";

function resolvePath(reqUrl, query) {
  let p = query && query.path;
  if (Array.isArray(p)) p = p.join("/");
  if (typeof p === "string" && p.length) {
    p = decodeURIComponent(p);
  } else {
    try {
      const u = new URL(reqUrl, "https://localhost");
      let pathname = u.pathname || "";
      pathname = pathname
        .replace(/^\/api\/proxy\/?/, "")
        .replace(/^\/api\//, "")
        .replace(/^\/+/, "");
      p = pathname && pathname !== "proxy" ? pathname : "";
    } catch (_) {
      p = "";
    }
  }
  p = String(p || "");
  // Reject absolute URLs / scheme tricks / traversal
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(p)) return ""; // http:, file:, etc
  if (p.includes("://") || p.includes("\\")) return "";
  p = p.replace(/^\/+/, "").replace(/\.\./g, "");
  // only safe path chars
  if (!/^[a-zA-Z0-9._~\-\/]*$/.test(p)) return "";
  return p;
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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function assertTargetSafe(target) {
  let u;
  try {
    u = new URL(target);
  } catch {
    return false;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return false;
  if (u.hostname !== ALLOWED_HOST) return false;
  if (u.username || u.password) return false;
  return true;
}

async function fetchUpstream(target) {
  let lastStatus = 0;
  let lastBuf = Buffer.alloc(0);
  let lastCt = "";
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const upstream = await fetch(target, {
        headers: {
          "User-Agent": UA,
          Accept: "application/json, text/plain, */*",
          "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
          Origin: "https://v1.voratoon.com",
          Referer: "https://v1.voratoon.com/",
        },
        redirect: "manual", // prevent redirect-to-internal SSRF
      });
      // disallow redirect off-host
      if (upstream.status >= 300 && upstream.status < 400) {
        const loc = upstream.headers.get("location") || "";
        if (!loc || !assertTargetSafe(new URL(loc, target).toString())) {
          return {
            status: 502,
            ct: "application/json",
            buf: Buffer.from(
              JSON.stringify({ error: "redirect_blocked", message: "Unsafe redirect" })
            ),
          };
        }
      }
      const ct = upstream.headers.get("content-type") || "";
      const buf = Buffer.from(await upstream.arrayBuffer());
      if (buf.length > 4 * 1024 * 1024) {
        return {
          status: 502,
          ct: "application/json",
          buf: Buffer.from(JSON.stringify({ error: "response_too_large" })),
        };
      }
      lastStatus = upstream.status;
      lastBuf = buf;
      lastCt = ct;
      if (upstream.status >= 500 && attempt < 3) {
        await sleep(400 * (attempt + 1));
        continue;
      }
      return { status: upstream.status, ct, buf };
    } catch (e) {
      if (attempt < 3) {
        await sleep(400 * (attempt + 1));
        continue;
      }
      throw e;
    }
  }
  return { status: lastStatus || 503, ct: lastCt, buf: lastBuf };
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();

  try {
    const subPath = resolvePath(req.url || "/", req.query || {});
    if (!subPath) {
      return res.status(400).json({
        error: "invalid_path",
        message: "Path relatif wajib; URL absolut ditolak (anti-SSRF).",
      });
    }

    const qs = collectQuery(req.query, req.url || "/");
    const q = qs.toString();
    // Lumen Railway exposes API under /api/*
    let apiPath = subPath.replace(/^api\//, "");
    const target = `${API_BASE}/api/${apiPath}${q ? `?${q}` : ""}`;
    if (!assertTargetSafe(target)) {
      return res.status(403).json({ error: "host_not_allowed" });
    }

    const { status, ct, buf } = await fetchUpstream(target);
    const textStart = buf.subarray(0, 80).toString("utf8").toLowerCase();

    if (
      ct.includes("text/html") ||
      textStart.includes("<!doctype") ||
      textStart.includes("just a moment")
    ) {
      return res.status(503).json({
        error: "upstream_blocked",
        message: "Upstream blocked or HTML challenge.",
        status,
      });
    }

    if (status >= 500) {
      return res.status(503).json({
        status: 503,
        error: "upstream_unavailable",
        message: "Server sumber sedang tidak tersedia (503).",
        path: subPath,
      });
    }

    res.setHeader("Content-Type", ct || "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "public, s-maxage=60, stale-while-revalidate=300");
    res.setHeader("X-Content-Type-Options", "nosniff");
    return res.status(status).send(buf);
  } catch (e) {
    return res.status(502).json({
      error: "proxy_error",
      message: String(e.message || e),
    });
  }
};
