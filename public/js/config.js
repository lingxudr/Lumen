/** App config — lokal vs produksi */
function detectEndpoints() {
  const host = typeof location !== "undefined" ? location.hostname : "";
  const local = host === "localhost" || host === "127.0.0.1" || host === "";

  // Jika dibuka langsung di Railway, API = same origin (paling stabil)
  if (host.endsWith(".railway.app")) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  const fromWindow =
    typeof window !== "undefined" && window.LUMEN_PROXY
      ? String(window.LUMEN_PROXY).trim()
      : "";

  if (local) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  if (!fromWindow) {
    console.warn("[Lumen] window.LUMEN_PROXY belum di-set");
    return { apiBase: "/api", imgProxy: "/img" };
  }
  const origin = fromWindow.replace(/\/$/, "");
  return {
    apiBase: origin + "/api",
    imgProxy: origin + "/img",
  };
}

const endpoints = detectEndpoints();

export const Config = {
  apiBase: endpoints.apiBase,
  imgProxy: endpoints.imgProxy,
  pageSize: 30,
  previewChapters: 3,
  version: "1.2.0",
};
