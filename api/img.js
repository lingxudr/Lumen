const UA =
  "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36";

const ALLOWED = [
  "imgkc1.my.id",
  "komikcast.fit",
  "komikcast.com",
  "voratoon.com",
  "voratoon.id",
  "cdn.voratoon.com",
  "cvr.voratoon.id",
  "assets.shngm.id",
  "minio.",
  "cdn.",
  "sv1.",
  "sv2.",
  "sv3.",
];

module.exports = async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Origin", "*");
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
    const accept =
      fmt === "webp"
        ? "image/webp,image/avif,image/apng,image/*,*/*;q=0.8"
        : "image/avif,image/webp,image/apng,image/*,*/*;q=0.8";

    const upstream = await fetch(src, {
      headers: {
        "User-Agent": UA,
        Accept: accept,
        Referer: "https://v1.voratoon.com/",
      },
    });
    const ct = upstream.headers.get("content-type") || "image/jpeg";
    const buf = Buffer.from(await upstream.arrayBuffer());
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Content-Type", ct);
    res.setHeader("Cache-Control", "public, max-age=604800, stale-while-revalidate=86400");
    res.setHeader("Vary", "Accept");
    if (ct.includes("webp")) res.setHeader("X-Lumen-Image", "webp");
    // Note: re-encode to WebP happens on Railway Python proxy (Pillow).
    // This serverless path streams origin bytes with long cache.
    return res.status(upstream.status).send(buf);
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
