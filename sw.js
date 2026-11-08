/* Matrix Control service worker: lets the installed app open with no internet.
   Network first (so updates arrive as soon as GitHub publishes them), cached copy when offline. */
const CACHE = "matrix-control-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./icon-maskable-192.png", "./icon-maskable-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;   /* weather/crypto/sports APIs go straight to the network */
  e.respondWith((async () => {
    try {
      const res = await fetch(req, { cache: "no-store" });
      if (res.ok) { const c = await caches.open(CACHE); c.put(req.mode === "navigate" ? "./index.html" : req, res.clone()); }
      return res;
    } catch {
      return (await caches.match(req.mode === "navigate" ? "./index.html" : req, { ignoreSearch: true })) || Response.error();
    }
  })());
});
