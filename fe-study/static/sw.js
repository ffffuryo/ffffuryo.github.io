// FE Study Service Worker: アプリ画面（シェル）をキャッシュし、電波が切れても起動できるようにする。
// /api/* はキャッシュしない（回答のオフライン保存は app.js の送信キューが担当）。
const CACHE = 'fe-study-v1';
const SHELL = ['/', '/static/app.js', '/static/app.css', '/static/manifest.webmanifest',
               '/static/icon.svg', '/static/icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  const isMedia = url.pathname.startsWith('/media/');
  // シェル: ネット優先（更新をすぐ反映）→ 失敗時キャッシュ。問題画像: キャッシュ優先。
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    if (isMedia) {
      const hit = await cache.match(e.request);
      if (hit) return hit;
    }
    try {
      const res = await fetch(e.request);
      if (res.ok) cache.put(e.request, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(e.request, { ignoreSearch: true });
      if (hit) return hit;
      if (e.request.mode === 'navigate') return cache.match('/');
      throw err;
    }
  })());
});
