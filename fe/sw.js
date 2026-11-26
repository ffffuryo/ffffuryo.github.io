// FE Study Service Worker: アプリ画面（シェル）をキャッシュし、電波が切れても起動できるようにする。
// すべて相対 URL（サブパス /fe/ でもサーバー版 / でも動く）。/api/ と packs/*.zip はキャッシュしない
// （パックは IndexedDB に入る。回答のオフライン保存はサーバー版では app.js の送信キューが担当）。
const CACHE = 'fe-study-v4';
const SHELL = ['./', 'index.html', 'static/app.js', 'static/app.css',
               'static/local/logic.js', 'static/local/store.js', 'static/local/api-local.js',
               'static/local/pack.js', 'static/local/backup.js',
               'static/manifest.webmanifest', 'static/icon.svg', 'static/icon-192.png', 'static/icon-512.png'];
const BASE = new URL('./', self.location).pathname;   // 例: './' または '/fe/'

self.addEventListener('install', (e) => {
  // 1 つ無くても install を失敗させない（サーバー版には local/*.js が無い）
  e.waitUntil(caches.open(CACHE)
    .then((c) => Promise.all(SHELL.map((u) => c.add(new Request(u, { cache: 'reload' })).catch(() => {}))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  const rel = url.pathname.startsWith(BASE) ? url.pathname.slice(BASE.length) : url.pathname.replace(/^\//, '');
  if (rel.startsWith('api/') || /^packs\/.*\.zip$/.test(rel)) return;
  const isMedia = rel.startsWith('media/');
  // シェル: ネット優先（更新をすぐ反映）→ 失敗時キャッシュ。問題画像（サーバー版）: キャッシュ優先。
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
      if (e.request.mode === 'navigate') return (await cache.match('./')) || (await cache.match('index.html'));
      throw err;
    }
  })());
});
