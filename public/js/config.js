/** App config — lokal vs produksi */
function detectEndpoints() {
  const host = typeof location !== "undefined" ? location.hostname : "";
  const local = host === "localhost" || host === "127.0.0.1" || host === "";

  // URL public Railway proxy (update setelah deploy Railway)
  // Contoh: https://lumen-proxy-production-xxxx.up.railway.app
  const fromWindow =
    typeof window !== "undefined" && window.LUMEN_PROXY
      ? String(window.LUMEN_PROXY).trim()
      : "";
  const RAILWAY_ORIGIN = fromWindow;

  if (local) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  // Produksi: wajib proxy Railway (Vercel IP sering diblokir sumber)
  if (!RAILWAY_ORIGIN) {
    console.warn("[Lumen] window.LUMEN_PROXY belum di-set — API mungkin gagal di Vercel");
    return { apiBase: "/api", imgProxy: "/img" };
  }
  const origin = RAILWAY_ORIGIN.replace(/\/$/, "");
  return {
    apiBase: origin + "/api",
    imgProxy: origin + "/img",
  };
}

const endpoints = detectEndpoints();

export const Config = {
  apiBase: endpoints.apiBase,
  imgProxy: endpoints.imgProxy,
  pageSize: 20,
  previewChapters: 3,
  version: "1.1.0",
};
