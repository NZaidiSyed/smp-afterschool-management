const CACHE_NAME = "kumon-tracking-cache-v11-workspaces";
const ASSETS = [
  "/",
  "/index.html",
  "/staffbase.html",
  "/staff-scheduler.html",
  "/staff-scheduler.css",
  "/staff-scheduler.js",
  "/app.js",
  "/student-navigation.js",
  "/attendance-ui.js",
  "/camera-scanner.js",
  "/vendor/zxing-library-0.21.3.min.js",
  "/styles.css",
  "/manifest.json"
];

// Install Service Worker immediately
self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log("[Service Worker] Caching app shell assets");
      return cache.addAll(ASSETS);
    })
  );
});

// Activate Service Worker, purge all old caches, and claim all open clients immediately
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log("[Service Worker] Removing old cache:", key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Cache only this application's static resources. Never return HTML for a failed script.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  event.respondWith((async () => {
    try {
      const response = await fetch(request);
      if (response.status === 200) {
        try {
          const cache = await caches.open(CACHE_NAME);
          await cache.put(request, response.clone());
        } catch (error) {
          // Storage limits must not turn a successful network response into a failure.
          console.warn("[Service Worker] Could not cache asset", error);
        }
      }
      return response;
    } catch (networkError) {
      const cached = await caches.match(request);
      if (cached) return cached;
      if (request.mode === "navigate") {
        const shell = await caches.match("/");
        if (shell) return shell;
      }
      throw networkError;
    }
  })());
});
