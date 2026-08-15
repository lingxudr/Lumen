/** App config — lokal vs produksi */
const DEFAULT_PROXY = "https://web-production-7769e.up.railway.app";

function detectEndpoints() {
  const host = typeof location !== "undefined" ? location.hostname : "";
  const local = host === "localhost" || host === "127.0.0.1" || host === "";

  // Railway same-origin
  if (host.endsWith(".railway.app")) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  const fromWindow =
    typeof window !== "undefined" && window.LUMEN_PROXY
      ? String(window.LUMEN_PROXY).trim()
      : "";

  // Hindari placeholder yang belum diganti
  const cleaned = fromWindow
    .replace(/lumen-production-xxxx\.up\.railway\.app/gi, "")
    .trim();

  if (local && !cleaned) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  const origin = (cleaned || DEFAULT_PROXY).replace(/\/$/, "");
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
  version: "1.2.1",
};
