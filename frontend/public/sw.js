/* PolyGIS service worker — makes the app installable (desktop and mobile) and keeps the shell working offline.
 * Deliberately conservative: it never touches the API or anything cross-origin, so sign-in and live data always hit
 * the network. It caches only same-origin GET static assets (stale-while-revalidate) and the app shell (index.html,
 * network-first with an offline fallback). Build assets are content-hashed, so new builds fetch fresh files on their
 * own; old entries are harmless. */
const CACHE = "polygis-shell-v1";
const SHELL = "/index.html";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.add(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;   // API/cross-origin: leave to the network
  if (url.pathname.startsWith("/api")) return;                               // never cache the API

  if (req.mode === "navigate") {                                             // pages: fresh when online, shell when not
    e.respondWith(fetch(req).catch(() => caches.match(SHELL)));
    return;
  }

  e.respondWith(                                                            // static assets: serve cached, refresh behind
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req).then((res) => {
        if (res && res.ok && res.type === "basic") cache.put(req, res.clone());
        return res;
      }).catch(() => cached);
      return cached || network;
    })
  );
});
