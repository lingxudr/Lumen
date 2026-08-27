/** App config — lokal vs produksi */
const DEFAULT_PROXY = "https://lumen-production-d82a.up.railway.app";

function detectEndpoints() {
  const host = typeof location !== "undefined" ? location.hostname : "";
  const local = host === "localhost" || host === "127.0.0.1" || host === "";

  // Same-origin: Railway backend atau Vercel (edge proxy → Railway)
  if (
    host.endsWith(".railway.app") ||
    host.endsWith(".vercel.app") ||
    host.includes("lumen")
  ) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  const fromWindow =
    typeof window !== "undefined" && window.LUMEN_PROXY
      ? String(window.LUMEN_PROXY).trim()
      : "";

  const cleaned = fromWindow
    .replace(/lumen-production-xxxx\.up\.railway\.app/gi, "")
    .trim();

  if (local && !cleaned) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  // Custom domain: prefer same-origin; optional absolute proxy via LUMEN_PROXY
  if (!cleaned) {
    return { apiBase: "/api", imgProxy: "/img" };
  }

  const origin = cleaned.replace(/\/$/, "");
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
  version: "1.2.2",
};
