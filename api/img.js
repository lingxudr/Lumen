/**
 * Lumen image proxy (Vercel)
 * - fmt=webp → Railway (Pillow re-encode) then long CDN cache
 * - otherwise stream origin with CDN-friendly headers
 * - ETag + 304 support
 */
const crypto = require("crypto");

const UA =
  "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36";

const UPSTREAM =
  process.env.LUMEN_UPSTREAM || "https://web-production-7769e.up.railway.app";

const ALLOWED = [
  "imgkc1.my.id",
  "komikcast.fit",
  "komikcast.com",
  "voratoon.com",
  "voratoon.id",
  "cdn.voratoon.com",
  "cvr.voratoon.id",
  "assets.shngm.id",
  "shngm.id",
  "minio.",
  "cdn.",
  "sv1.",
  "sv2.",
  "sv3.",
];

function etagFor(buf) {
  return '"' + crypto.createHash("sha1").update(buf).digest("hex").slice(0, 20) + '"';
}

function setCdnHeaders(res, { webp, etag, hit }) {
  // Browser cache 7d; shared/CDN cache 30d; SWR 7d
  const browser = webp ? 604800 : 259200;
  const shared = webp ? 2592000 : 604800;
  res.setHeader(
    "Cache-Control",
    `public, max-age=${browser}, s-maxage=${shared}, stale-while-revalidate=604800`
  );
  // Vercel / Cloudflare explicit CDN directives
  res.setHeader(
    "CDN-Cache-Control",
    `public, max-age=${shared}, stale-while-revalidate=604800`
  );
  res.setHeader(
    "Vercel-CDN-Cache-Control",
    `public, max-age=${shared}, stale-while-revalidate=604800`
  );
  res.setHeader("Vary", "Accept");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Expose-Headers", "X-Lumen-Image, X-Lumen-Cache, ETag");
  if (etag) {
    res.setHeader("ETag", etag);
  }
  if (hit) res.setHeader("X-Lumen-Cache", hit);
  if (webp) res.setHeader("X-Lumen-Image", "webp");
}

module.exports = async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Accept, If-None-Match");
    return res.status(204).end();
  }

  try {
    const url = new URL(req.url, "http://localhost");
    const src = url.searchParams.get("u") || "";
    if (!src.startsWith("http://") && !src.startsWith("https://")) {
      return res.status(400).json({ error: "missing u" });
    }
    if (!ALLOWED.some((a) => src.includes(a))) {
      return res.status(403).json({ error: "host not allowed" });
    }

    const fmt = (url.searchParams.get("fmt") || "").toLowerCase();
    const wantWebp = fmt === "webp";
    const w = url.searchParams.get("w") || "";

    // Always via Railway /img — memory+disk cache + optional WebP
    const qs = new URLSearchParams();
    qs.set("u", src);
    if (wantWebp) qs.set("fmt", "webp");
    if (w) qs.set("w", w);
    const railUrl = `${UPSTREAM.replace(/\/$/, "")}/img?${qs.toString()}`;
    const via = wantWebp ? "railway-webp" : "railway-cache";
    const upstream = await fetch(railUrl, {
      headers: {
        "User-Agent": UA,
        Accept: wantWebp
          ? "image/webp,image/*,*/*;q=0.8"
          : "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "If-None-Match": req.headers["if-none-match"] || "",
      },
    });

    if (upstream.status === 304) {
      setCdnHeaders(res, {
        webp: wantWebp,
        etag: req.headers["if-none-match"] || "",
        hit: "REVALIDATED",
      });
      return res.status(304).end();
    }
    if (!upstream.ok) {
      return res.status(upstream.status >= 400 ? upstream.status : 502).json({
        error: "upstream_image_failed",
        status: upstream.status,
      });
    }

    const ct =
      upstream.headers.get("content-type") ||
      (wantWebp ? "image/webp" : "image/jpeg");
    const buf = Buffer.from(await upstream.arrayBuffer());
    const etag = etagFor(buf);

    const inm = req.headers["if-none-match"];
    if (inm && inm === etag) {
      setCdnHeaders(res, { webp: ct.includes("webp"), etag, hit: "REVALIDATED" });
      return res.status(304).end();
    }

    const isWebp = ct.includes("webp");
    setCdnHeaders(res, { webp: isWebp, etag, hit: "MISS" });
    res.setHeader("Content-Type", ct);
    res.setHeader("X-Lumen-Via", via);
    if (!isWebp && wantWebp) {
      res.setHeader("X-Lumen-Image", "jpeg-fallback");
    }
    return res.status(200).send(buf);
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
