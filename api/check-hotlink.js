const UA =
  "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36";

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });

  try {
    let body = req.body;
    if (typeof body === "string") body = JSON.parse(body || "{}");
    if (!body || typeof body !== "object") body = {};
    let urls = body.urls || [];
    if (typeof urls === "string") urls = [urls];
    urls = urls.filter((u) => typeof u === "string" && u.startsWith("http")).slice(0, 8);
    if (!urls.length) return res.status(400).json({ error: "provide urls" });

    const strategies = [
      ["no_referer", null],
      ["empty_referer", ""],
      ["voratoon_referer", "https://v1.voratoon.com/"],
      ["foreign_referer", "https://example.com/"],
    ];

    const results = [];
    for (const url of urls) {
      const entry = { url, tests: [] };
      for (const [name, referer] of strategies) {
        const headers = { "User-Agent": UA, Accept: "image/*,*/*;q=0.8" };
        if (referer !== null) headers.Referer = referer;
        try {
          const r = await fetch(url, { headers });
          const ab = await r.arrayBuffer();
          const chunk = Buffer.from(ab).subarray(0, 2048);
          const ct = r.headers.get("content-type") || "";
          const ok =
            r.status === 200 &&
            (ct.startsWith("image/") ||
              chunk[0] === 0xff ||
              (chunk[0] === 0x89 && chunk[1] === 0x50));
          entry.tests.push({
            strategy: name,
            status: r.status,
            content_type: ct,
            bytes_sample: chunk.length,
            ok_image: !!ok,
          });
        } catch (e) {
          entry.tests.push({
            strategy: name,
            status: null,
            ok_image: false,
            error: String(e.message || e),
          });
        }
      }
      const oks = Object.fromEntries(entry.tests.map((t) => [t.strategy, t.ok_image]));
      if (oks.no_referer || oks.empty_referer || oks.foreign_referer) {
        entry.verdict = oks.voratoon_referer ? "open" : "mixed";
      } else if (oks.voratoon_referer) {
        entry.verdict = "hotlink_protected";
      } else {
        entry.verdict = "blocked";
      }
      results.push(entry);
    }
    return res.status(200).json({ results });
  } catch (e) {
    return res.status(500).json({ error: String(e.message || e) });
  }
};
