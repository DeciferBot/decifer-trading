// M11B.4 SW kill-switch — replaces any previous service worker.
// Immediately activates and unregisters itself so no content is ever served from cache.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", () => {
  self.registration.unregister().then(() => self.clients.matchAll()).then(clients => {
    clients.forEach(client => client.navigate(client.url));
  });
});
