/* @module static/sw.js
 * @purpose Offline-first PWA Service Worker caching shell assets and bypassing live API/MCP streams.
 * @inputs Fetch, install, and activate ServiceWorker events.
 * @outputs Cached shell responses for Greenroom Companion v0.3.0. */

const CACHE_VERSION = "greenroom-companion-v0.3.0";
const CACHE_NAME = "greenroom-companion-v030";

const SHELL_ASSETS = [
  "./",
  "./favicon.ico",
  "./static/styles.css",
  "./static/styles.css?v=0.3.0",
  "./static/app.js",
  "./static/app.js?v=0.3.0",
  "./static/icons/icon.svg",
  "./static/icons/icon-192.png",
  "./static/icons/icon-512.png",
  "./manifest.webmanifest"
];

self.addEventListener("install", (event) => {
  const installPromise = caches
    .open(CACHE_NAME)
    .then((cache) => cache.addAll(SHELL_ASSETS))
    .then(() => self.skipWaiting());
  return event.waitUntil(installPromise);
});

self.addEventListener("activate", (event) => {
  const activatePromise = caches
    .keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME && k !== CACHE_VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim());
  return event.waitUntil(activatePromise);
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (/\/api\/|\/mcp(\/|$)|\/mcp-sse(\/|$)/.test(url.pathname)) {
    return;
  }
  const responsePromise = fetch(event.request)
    .then((resp) => {
      if (event.request.method === "GET" && resp.status === 200) {
        const clone = resp.clone();
        caches
          .open(CACHE_NAME)
          .then((cache) => cache.put(event.request, clone))
          .catch((err) => console.debug("SW cache update skipped:", err));
      }
      return resp;
    })
    .catch(() => caches.match(event.request));
  return event.respondWith(responsePromise);
});
