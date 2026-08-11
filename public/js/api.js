import { Config } from "./config.js";

/** In-memory request cache + in-flight dedup */
const MEM = new Map(); // key -> { exp, data, staleExp }
const INFLIGHT = new Map(); // key -> Promise

const DEFAULT_TTL = 90_000; // 90s fresh
const DEFAULT_STALE = 10 * 60_000; // stale up to 10m while revalidating
const FETCH_TIMEOUT = 18_000;
const MAX_RETRIES = 2;

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

async function fetchJsonOnce(url, signal) {
  let res;
  try {
    res = await fetch(url, { signal });
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error("Koneksi timeout. Coba lagi.");
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      throw new Error("Tidak ada koneksi internet.");
    }
    throw new Error("Tidak dapat terhubung ke server. Coba beberapa saat lagi.");
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
    if (data.error === "upstream_blocked" || data.message) {
      throw new Error(data.message || data.error);
    }
    throw new Error(data.message || data.error || `Gagal memuat (${res.status})`);
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
      if (attempt < MAX_RETRIES) await sleep(400 * (attempt + 1));
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

export function proxyImageUrl(url) {
  return `${Config.imgProxy}?u=${encodeURIComponent(url)}`;
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
