import { Config } from "./config.js";

const FALLBACK_API_ORIGIN = "https://lumen-production-d82a.up.railway.app";


/** In-memory request cache + in-flight dedup */
const MEM = new Map(); // key -> { exp, data, staleExp }
const INFLIGHT = new Map(); // key -> Promise

const DEFAULT_TTL = 90_000; // 90s fresh
const DEFAULT_STALE = 5 * 60_000; // stale max ~5m (selaras server hard TTL list)
const FETCH_TIMEOUT = 32_000;
const MAX_RETRIES = 3;

function cacheKey(path, params) {
  const qs = new URLSearchParams();
  Object.keys(params || {})
    .sort()
    .forEach((k) => {
      const v = params[k];
      if (v !== undefined && v !== null && v !== "") qs.set(k, v);
    });
  return `${path}?${qs}`;
}

function buildUrl(path, params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  });
  return `${Config.apiBase}/${path}${qs.toString() ? `?${qs}` : ""}`;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export function friendlyError(err) {
  const s = String(err || "");
  if (/Failed to fetch|NetworkError|Load failed|network/i.test(s)) {
    return "Tidak ada koneksi atau server tidak merespons. Coba lagi.";
  }
  if (/timeout|AbortError|aborted/i.test(s)) {
    return "Koneksi timeout — server mungkin sedang bangun. Coba lagi.";
  }
  if (/503|upstream_unavailable|unavailable/i.test(s)) {
    return "Sumber data sedang sibuk. Coba beberapa detik lagi.";
  }
  if (/429|rate_limited/i.test(s)) {
    return "Terlalu banyak permintaan. Tunggu sebentar.";
  }
  if (/404|not found|tidak ditemukan/i.test(s)) {
    return "Data tidak ditemukan.";
  }
  if (/kosong|empty|no data|tidak ada/i.test(s)) {
    return "Tidak ada data untuk ditampilkan.";
  }
  if (/Chapter gagal|gagal memuat chapter/i.test(s)) {
    return "Chapter gagal dimuat. Coba lagi atau pilih chapter lain.";
  }
  // strip technical noise
  if (s.length > 120) return s.slice(0, 117) + "…";
  return s || "Terjadi kesalahan.";
}

function shouldTryRailwayFallback(url, data, err) {
  try {
    if (typeof location === "undefined") return false;
    const h = location.hostname || "";
    if (h.endsWith(".railway.app")) return false;
    if (!String(url || "").includes("/api")) return false;
    if (err) return true;
    if (!data || typeof data !== "object") return true;
    if (Array.isArray(data.data) && data.data.length === 0 && /series/.test(String(url))) return true;
    if (Number(data.status) >= 500) return true;
  } catch (_) {}
  return false;
}
function toRailwayUrl(url) {
  try {
    const u = new URL(url, typeof location !== "undefined" ? location.origin : FALLBACK_API_ORIGIN);
    return FALLBACK_API_ORIGIN + u.pathname + u.search;
  } catch (_) {
    return FALLBACK_API_ORIGIN + "/api/series";
  }
}
async function fetchJsonOnce(url, signal) {
  let res;
  try {
    res = await fetch(url, { signal });
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error(friendlyError("timeout"));
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      throw new Error("Tidak ada koneksi internet.");
    }
    throw new Error(friendlyError(e && e.message));
  }

  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Respons tidak valid (${res.status})`);
  }

  if (res.status === 429) {
    const wait = data.retry_after || 5;
    throw new Error(`Terlalu banyak permintaan. Tunggu ~${wait}s.`);
  }

  if (!res.ok) {
    if (data.error === "rate_limited") {
      throw new Error(`Terlalu banyak permintaan. Tunggu ~${data.retry_after || 5}s.`);
    }
    throw new Error(
      friendlyError(data.message || data.error || data.detail || `Gagal memuat (${res.status})`)
    );
  }
  return data;
}

async function fetchJsonCore(url) {
  let lastErr;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), FETCH_TIMEOUT) : null;
    try {
      const data = await fetchJsonOnce(url, ctrl ? ctrl.signal : undefined);
      if (timer) clearTimeout(timer);
      return data;
    } catch (e) {
      if (timer) clearTimeout(timer);
      lastErr = e;
      const msg = String(e && e.message ? e.message : e);
      // jangan retry error logis (404-ish message, rate limit)
      if (/Terlalu banyak|tidak valid|tidak ditemukan/i.test(msg)) break;
      if (attempt < MAX_RETRIES) await sleep(900 * (attempt + 1) + Math.random() * 400);
    }
  }
  throw lastErr || new Error("Gagal memuat data");
}

/**
 * @param {string} path
 * @param {object} params
 * @param {{ ttl?: number, stale?: number, force?: boolean }} [opts]
 */

async function fetchJson(url) {
  try {
    const data = await fetchJsonCore(url);
    if (
      shouldTryRailwayFallback(url, data, null) &&
      !String(url).includes("lumen-production-d82a")
    ) {
      const alt = toRailwayUrl(url);
      console.warn("[lumen] empty/fail same-origin, retry Railway", alt);
      return await fetchJsonCore(alt);
    }
    return data;
  } catch (err) {
    if (
      shouldTryRailwayFallback(url, null, err) &&
      !String(url).includes("lumen-production-d82a")
    ) {
      const alt = toRailwayUrl(url);
      console.warn("[lumen] same-origin error, retry Railway", err && err.message);
      return await fetchJsonCore(alt);
    }
    throw err;
  }
}

export async function api(path, params = {}, opts = {}) {
  const ttl = opts.ttl != null ? opts.ttl : DEFAULT_TTL;
  const stale = opts.stale != null ? opts.stale : DEFAULT_STALE;
  const key = cacheKey(path, params);
  const url = buildUrl(path, params);
  const now = Date.now();

  if (!opts.force) {
    const hit = MEM.get(key);
    if (hit && hit.exp > now) return hit.data;

    if (hit && hit.staleExp > now) {
      if (!INFLIGHT.has(key)) {
        const p = fetchJson(url)
          .then((data) => {
            MEM.set(key, { data, exp: Date.now() + ttl, staleExp: Date.now() + stale });
            return data;
          })
          .catch(() => hit.data)
          .finally(() => INFLIGHT.delete(key));
        INFLIGHT.set(key, p);
      }
      return hit.data;
    }
  }

  if (INFLIGHT.has(key) && !opts.force) return INFLIGHT.get(key);

  const p = fetchJson(url)
    .then((data) => {
      MEM.set(key, { data, exp: Date.now() + ttl, staleExp: Date.now() + stale });
      // bound memory
      if (MEM.size > 120) {
        const first = MEM.keys().next().value;
        MEM.delete(first);
      }
      return data;
    })
    .finally(() => INFLIGHT.delete(key));
  INFLIGHT.set(key, p);
  return p;
}

export function apiPeek(path, params = {}) {
  const key = cacheKey(path, params);
  const hit = MEM.get(key);
  if (!hit || hit.staleExp < Date.now()) return null;
  return hit.data;
}

export function apiPrefetch(path, params = {}, opts = {}) {
  api(path, params, opts).catch(() => {});
}

export function clearApiCache() {
  MEM.clear();
  INFLIGHT.clear();
}

function supportsWebP() {
  if (supportsWebP._v != null) return supportsWebP._v;
  // Optimistic: modern mobile browsers all support WebP.
  // Only disable if canvas probe explicitly fails.
  try {
    if (typeof document === "undefined") {
      supportsWebP._v = true;
      return true;
    }
    supportsWebP._v =
      document.createElement("canvas").toDataURL("image/webp").indexOf("data:image/webp") === 0;
  } catch {
    supportsWebP._v = true;
  }
  return supportsWebP._v;
}

function isAlreadyWebp(url) {
  try {
    const u = String(url).split("?")[0].toLowerCase();
    return u.endsWith(".webp");
  } catch {
    return false;
  }
}

/**
 * Build image proxy URL with optional WebP + width.
 * @param {string} url
 * @param {{ webp?: boolean, w?: number }} [opts]
 */
export function proxyImageUrl(url, opts = {}) {
  if (!url) return "";
  const qs = new URLSearchParams();
  qs.set("u", url);
  // P0: always prefer WebP unless caller opts.webp === false
  if (opts.webp !== false) qs.set("fmt", "webp");
  let w = opts.w;
  if (w == null && typeof window !== "undefined") {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const vw = window.innerWidth || 400;
    // Cover grid ~2 columns on mobile → ~half viewport
    if (opts.cover) {
      w = Math.round(Math.min(480, Math.max(180, (vw / 2) * dpr)));
    } else if (vw > 0 && vw <= 480) {
      w = Math.round(vw * dpr);
    } else if (vw <= 900) {
      w = Math.round(Math.min(900, vw) * dpr);
    }
  }
  if (w) qs.set("w", String(Math.max(160, Math.min(1600, Number(w) || 0))));
  if (isAlreadyWebp(url)) qs.set("src", "webp");
  return `${Config.imgProxy}?${qs}`;
}

export async function checkImageStatus(urls) {
  const res = await fetch(`${Config.apiBase}/check-hotlink`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ urls }),
  });
  const data = JSON.parse((await res.text()) || "{}");
  if (!res.ok) throw new Error(data.error || "Pemeriksaan gagal");
  return data.results || [];
}
