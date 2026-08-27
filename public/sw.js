/* Lumen service worker v7 — shell only; never cache API/JSON chapter data */
const VERSION = "v7";
const SHELL_CACHE = `lumen-shell-${VERSION}`;
const IMG_CACHE = `lumen-img-${VERSION}`;
const IMG_MAX = 48;

const SHELL = [
  "/",
  "/index.html",
  "/css/main.css",
  "/css/reader-v2.css",
  "/js/app.js",
  "/js/config.js",
  "/js/api.js",
  "/js/ui.js",
  "/js/utils.js",
  "/js/storage.js",
  "/js/router.js",
  "/js/seo.js",
  "/js/views/home.js",
  "/js/views/series.js",
  "/js/views/reader.js",
  "/js/views/library.js",
  "/manifest.webmanifest",
  "/assets/icon-192.png",
  "/assets/icon-512.png",
  "/assets/apple-touch-icon.png",
];

function isSameOrigin(url) {
  return url.origin === self.location.origin;
}

function isApiOrProxy(url) {
  const p = url.pathname;
  // Never cache JSON/API/chapter/pages/proxy — always network
  if (p.startsWith("/api/") || p === "/api") return true;
  if (p === "/img" || p.startsWith("/img?")) return true;
  if (p.includes("/series/") && p.includes("/chapters")) return true;
  return false;
}

function isStaticAsset(url) {
  return (
    url.pathname.startsWith("/css/") ||
    url.pathname.startsWith("/js/") ||
    url.pathname.startsWith("/assets/") ||
    url.pathname.endsWith(".webmanifest")
  );
}

function isCacheableResponse(res) {
  if (!res || !res.ok) return false;
  if (res.status === 206) return false;
  if (res.type !== "basic" && res.type !== "cors") return false;
  const ct = (res.headers.get("Content-Type") || "").toLowerCase();
  // Never put JSON into SW cache (stale chapters risk)
  if (ct.includes("application/json")) return false;
  return true;
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  const overflow = keys.length - maxEntries;
  for (let i = 0; i < overflow; i++) {
    await cache.delete(keys[i]);
  }
}

async function networkFirst(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(request);
    if (isCacheableResponse(res)) {
      await cache.put(request, res.clone());
    }
    return res;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    if (fallbackUrl) {
      const fb = await cache.match(fallbackUrl);
      if (fb) return fb;
    }
    throw new Error("offline");
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) {
    fetch(request)
      .then(async (res) => {
        if (isCacheableResponse(res)) await cache.put(request, res.clone());
      })
      .catch(() => {});
    return cached;
  }
  const res = await fetch(request);
  if (isCacheableResponse(res)) await cache.put(request, res.clone());
  return res;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) =>
        Promise.all(SHELL.map((url) => cache.add(url).catch(() => {})))
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== IMG_CACHE)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }

  // API / img / chapter JSON — network only, no SW cache
  if (isApiOrProxy(url)) return;

  if (!isSameOrigin(url)) return;

  const accept = req.headers.get("Accept") || "";
  const isNav =
    req.mode === "navigate" ||
    (req.destination === "document" && accept.includes("text/html"));

  if (isNav) {
    event.respondWith(networkFirst(req, SHELL_CACHE, "/"));
    return;
  }

  // Local icons only (not /img proxy)
  if (
    (req.destination === "image" || /\.(png|jpe?g|webp|gif|svg|avif)$/i.test(url.pathname)) &&
    url.pathname.startsWith("/assets/")
  ) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(IMG_CACHE);
        const cached = await cache.match(req);
        try {
          const res = await fetch(req);
          if (isCacheableResponse(res)) {
            await cache.put(req, res.clone());
            await trimCache(IMG_CACHE, IMG_MAX);
          }
          return res;
        } catch {
          if (cached) return cached;
          return new Response("", { status: 503, statusText: "offline" });
        }
      })()
    );
    return;
  }

  if (isStaticAsset(url) || req.destination === "style" || req.destination === "script") {
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }

  event.respondWith(
    fetch(req).catch(() => caches.match(req).then((c) => c || Response.error()))
  );
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data && data.type === "CLEAR_IMG_CACHE") {
    event.waitUntil(caches.delete(IMG_CACHE).then(() => caches.open(IMG_CACHE)));
  }
  if (data && data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
