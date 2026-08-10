/* Lumen service worker — cache shell + cover offline */
const CACHE = "lumen-shell-v1";
const SHELL = ["/", "/index.html", "/css/main.css", "/js/app.js", "/js/config.js", "/js/api.js", "/js/ui.js", "/js/utils.js", "/js/storage.js", "/js/views/home.js", "/js/views/series.js", "/js/views/reader.js", "/js/views/library.js", "/manifest.webmanifest", "/assets/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // API — network only
  if (url.pathname.startsWith("/api/") || url.pathname === "/img") return;

  // Covers / images — cache on success (stale-while-revalidate style)
  if (req.destination === "image") {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const fetchPromise = fetch(req)
          .then((res) => {
            if (res.ok) cache.put(req, res.clone());
            return res;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
    return;
  }

  // App shell — cache first, then network
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req)
        .then((res) => {
          if (res.ok && url.origin === self.location.origin) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => cached);
      return cached || fetched;
    })
  );
});
