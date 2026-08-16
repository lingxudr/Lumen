/* Lumen service worker — app shell offline */
const CACHE = "lumen-shell-v3";
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
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // API + image proxy — network only (fresh data)
  if (url.pathname.startsWith("/api/") || url.pathname === "/img" || url.pathname.startsWith("/img?")) {
    return;
  }

  // Cross-origin — don't intercept
  if (url.origin !== self.location.origin) return;

  // Images — cache successful responses
  if (req.destination === "image") {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        try {
          const res = await fetch(req);
          if (res.ok) cache.put(req, res.clone());
          return res;
        } catch {
          if (cached) return cached;
          throw new Error("offline image");
        }
      })
    );
    return;
  }

  // Shell — network first, fallback cache (avoid stale UI forever)
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((c) => c || caches.match("/")))
  );
});
