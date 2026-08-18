import { Config } from "./config.js";

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

function friendlyError(raw) {
  const s = String(raw || "");
  if (/upstream_unavailable|no available server|503/i.test(s)) {
    return "Server sumber sedang down (503). Coba lagi beberapa menit.";
  }
  if (/Application failed to respond|502|upstream/i.test(s)) {
    return "Server sibuk atau sedang restart. Coba lagi sebentar.";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(s)) {
    return "Server sedang bangun atau jaringan terputus. Tunggu ~10 detik lalu coba lagi.";
  }
  if (/Application failed to respond|502 Bad Gateway|504/i.test(s)) {
    return "Server baru saja aktif kembali. Muat ulang sebentar lagi.";
  }
  if (/rate_limited|Terlalu banyak/i.test(s)) {
    return "Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.";
  }
  if (/timeout|AbortError/i.test(s)) {
    return "Koneksi timeout. Coba lagi.";
  }
  return s || "Terjadi kesalahan. Coba lagi.";
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

async function fetchJson(url) {
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
  const wantWebp = opts.webp !== false;
  // Request WebP for covers/UI; server falls back to original only if encode fails
  if (wantWebp && supportsWebP()) qs.set("fmt", "webp");
  else if (wantWebp) qs.set("fmt", "webp"); // still ask; server may serve webp
  let w = opts.w;
  if (w == null && typeof window !== "undefined") {
    // Auto width for mobile reader: save data on narrow screens
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const vw = window.innerWidth || 400;
    if (vw > 0 && vw <= 480) w = Math.round(vw * dpr);
    else if (vw <= 900) w = Math.round(Math.min(900, vw) * dpr);
  }
  if (w) qs.set("w", String(Math.max(240, Math.min(1600, Number(w) || 0))));
  // cache-bust key helper for already-webp sources (still go through proxy for hotlink)
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
