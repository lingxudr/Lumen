import { Config } from "./config.js";

/** In-memory request cache + in-flight dedup */
const MEM = new Map(); // key -> { exp, data, staleExp }
const INFLIGHT = new Map(); // key -> Promise

const DEFAULT_TTL = 60_000; // 60s fresh
const DEFAULT_STALE = 5 * 60_000; // serve stale up to 5m while revalidating

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

async function fetchJson(url) {
  let res;
  try {
    res = await fetch(url);
  } catch {
    throw new Error("Tidak dapat terhubung ke server. Pastikan aplikasi sedang berjalan.");
  }
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Respons tidak valid (${res.status})`);
  }
  if (!res.ok) {
    if (data.error === "upstream_blocked" || data.message) {
      throw new Error(data.message || data.error);
    }
    throw new Error(data.message || data.error || `Gagal memuat (${res.status})`);
  }
  return data;
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
    if (hit && hit.exp > now) {
      return hit.data;
    }
    // stale-while-revalidate
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

  if (INFLIGHT.has(key) && !opts.force) {
    return INFLIGHT.get(key);
  }

  const p = fetchJson(url)
    .then((data) => {
      MEM.set(key, { data, exp: Date.now() + ttl, staleExp: Date.now() + stale });
      return data;
    })
    .finally(() => INFLIGHT.delete(key));
  INFLIGHT.set(key, p);
  return p;
}

/** Peek cache without network */
export function apiPeek(path, params = {}) {
  const key = cacheKey(path, params);
  const hit = MEM.get(key);
  if (!hit) return null;
  if (hit.staleExp < Date.now()) return null;
  return hit.data;
}

/** Warm cache in background (no throw to caller) */
export function apiPrefetch(path, params = {}, opts = {}) {
  api(path, params, opts).catch(() => {});
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
