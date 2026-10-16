// SMP Service Worker - v20261017
// Caching is DISABLED. This SW clears all old caches on activate.
// All assets are served fresh from the network on every request.

self.addEventListener('install', function() {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.map(function(key) { return caches.delete(key); }));
    }).then(function() { return self.clients.claim(); })
  );
});

// No fetch handler - all requests go straight to the network.
