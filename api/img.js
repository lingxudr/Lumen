/**
 * Lumen image proxy (Vercel)
 * 1) Prefer Railway /img (cache + WebP)
 * 2) On 403/502 from Railway → fetch origin CDN directly (reader still works)
 * 3) Long CDN cache + ETag
 */
const crypto = require("crypto");

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36";

const UPSTREAM =
  process.env.LUMEN_UPSTREAM || "https://lumen-production-d82a.up.railway.app";

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
  const browser = webp ? 604800 : 259200;
  const shared = webp ? 2592000 : 604800;
  res.setHeader(
    "Cache-Control",
    `public, max-age=${browser}, s-maxage=${shared}, stale-while-revalidate=604800`
  );
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
  res.setHeader(
    "Access-Control-Expose-Headers",
    "X-Lumen-Image, X-Lumen-Cache, ETag, X-Lumen-Via"
  );
  if (etag) res.setHeader("ETag", etag);
  if (hit) res.setHeader("X-Lumen-Cache", hit);
  if (webp) res.setHeader("X-Lumen-Image", "webp");
}

async function fetchOrigin(src) {
  const referers = [
    "https://v2.voratoon.com/",
    "https://www.voratoon.com/",
    "https://cdn.voratoon.com/",
    "",
  ];
  let lastStatus = 0;
  for (const ref of referers) {
    const headers = {
      "User-Agent": UA,
      Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
      "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    };
    if (ref) {
      headers.Referer = ref;
      headers.Origin = "https://v2.voratoon.com";
    }
    try {
      const r = await fetch(src, { headers, redirect: "follow" });
      lastStatus = r.status;
      if (r.ok) {
        const buf = Buffer.from(await r.arrayBuffer());
        if (buf.length > 200) {
          const ct = r.headers.get("content-type") || "image/jpeg";
          return { ok: true, status: r.status, buf, ct };
        }
      }
      if (![401, 403, 429].includes(r.status)) break;
    } catch (e) {
      lastStatus = 0;
    }
  }
  return { ok: false, status: lastStatus || 502, buf: null, ct: null };
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

    // 1) Railway path (cache + WebP)
    const qs = new URLSearchParams();
    qs.set("u", src);
    if (wantWebp) qs.set("fmt", "webp");
    if (w) qs.set("w", w);
    const railUrl = `${UPSTREAM.replace(/\/$/, "")}/img?${qs.toString()}`;

    let upstream;
    try {
      upstream = await fetch(railUrl, {
        headers: {
          "User-Agent": UA,
          Accept: wantWebp
            ? "image/webp,image/*,*/*;q=0.8"
            : "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
          "If-None-Match": req.headers["if-none-match"] || "",
        },
      });
    } catch (e) {
      upstream = null;
    }

    if (upstream && upstream.status === 304) {
      setCdnHeaders(res, {
        webp: wantWebp,
        etag: req.headers["if-none-match"] || "",
        hit: "REVALIDATED",
      });
      return res.status(304).end();
    }

    if (upstream && upstream.ok) {
      const ct =
        upstream.headers.get("content-type") ||
        (wantWebp ? "image/webp" : "image/jpeg");
      const buf = Buffer.from(await upstream.arrayBuffer());
      const etag = etagFor(buf);
      const inm = req.headers["if-none-match"];
      if (inm && inm === etag) {
        setCdnHeaders(res, {
          webp: ct.includes("webp"),
          etag,
          hit: "REVALIDATED",
        });
        return res.status(304).end();
      }
      const isWebp = ct.includes("webp");
      setCdnHeaders(res, { webp: isWebp, etag, hit: "MISS" });
      res.setHeader("Content-Type", ct);
      res.setHeader("X-Lumen-Via", wantWebp ? "railway-webp" : "railway-cache");
      if (!isWebp && wantWebp) res.setHeader("X-Lumen-Image", "jpeg-fallback");
      return res.status(200).send(buf);
    }

    // 2) Fallback: origin CDN from Vercel edge (Railway IP often blocked → 403)
    const origin = await fetchOrigin(src);
    if (origin.ok) {
      const etag = etagFor(origin.buf);
      setCdnHeaders(res, {
        webp: (origin.ct || "").includes("webp"),
        etag,
        hit: "ORIGIN",
      });
      res.setHeader("Content-Type", origin.ct || "image/jpeg");
      res.setHeader("X-Lumen-Via", "vercel-origin");
      if (wantWebp && !(origin.ct || "").includes("webp")) {
        res.setHeader("X-Lumen-Image", "jpeg-fallback");
      }
      return res.status(200).send(origin.buf);
    }

    const st =
      (upstream && upstream.status) || origin.status || 502;
    return res.status(st >= 400 && st < 600 ? st : 502).json({
      error: "upstream_image_failed",
      status: st,
      hint: "CDN blocked proxy IP; try again later",
    });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
