import { Config } from "./config.js";

/**
 * HTTP client ke backend proxy.
 */
export async function api(path, params = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  });
  const url = `${Config.apiBase}/${path}${qs.toString() ? `?${qs}` : ""}`;

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
