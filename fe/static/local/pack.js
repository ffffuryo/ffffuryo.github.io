/* FE Study — データパック（無圧縮 ZIP）の読み込み。SPEC 7.2 / 7.3 / 7.4
 * window.FEPack: installPack / packStatus / downloadPublicPack / fetchIndex / clearAll
 * IndexedDB 'fe-study' v1 を直接開く（store.js と同じスキーマ。先に開いた方が作成する）。 */
(function (root) {
  'use strict';
  const DB_NAME = 'fe-study', DB_VER = 1;
  const STORES = [
    ['questions', { keyPath: 'id' }, [['part', 'part'], ['pool', 'pool']]],
    ['blobs', { keyPath: 'path' }, []],
    ['sessions', { keyPath: 'id', autoIncrement: true }, [['status', 'status']]],
    ['attempts', { keyPath: 'id', autoIncrement: true }, [['question_id', 'question_id'], ['session_id', 'session_id']]],
    ['settings', { keyPath: 'key' }, []],
    ['meta', { keyPath: 'key' }, []],
  ];
  let dbp = null;
  function openDb() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      const rq = indexedDB.open(DB_NAME, DB_VER);
      rq.onupgradeneeded = () => {
        const db = rq.result;
        for (const [name, opt, idx] of STORES) {
          if (db.objectStoreNames.contains(name)) continue;
          const os = db.createObjectStore(name, opt);
          for (const [n, kp] of idx) os.createIndex(n, kp);
        }
      };
      rq.onsuccess = () => resolve(rq.result);
      rq.onerror = () => { dbp = null; reject(rq.error); };
    });
    return dbp;
  }
  const reqP = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const txDone = (tx) => new Promise((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error || new Error('tx aborted')); });
  async function putMany(store, rows) {
    const db = await openDb();
    const tx = db.transaction(store, 'readwrite');
    const os = tx.objectStore(store);
    for (const r of rows) os.put(r);
    await txDone(tx);
  }
  async function getAll(store) { const db = await openDb(); return reqP(db.transaction(store).objectStore(store).getAll()); }
  async function getOne(store, key) { const db = await openDb(); return reqP(db.transaction(store).objectStore(store).get(key)); }
  const tick = () => new Promise((r) => setTimeout(r, 0));

  /* ---------- 無圧縮 ZIP リーダー ---------- */
  function readZip(buf) {
    const u8 = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    let eocd = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 22 - 65535); i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('ZIP ファイルではありません');
    const count = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const dec = new TextDecoder('utf-8');
    const entries = new Map();
    for (let n = 0; n < count; n++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new Error('ZIP の中央ディレクトリが壊れています');
      const method = dv.getUint16(p + 10, true);
      const size = dv.getUint32(p + 24, true);
      const nl = dv.getUint16(p + 28, true), el = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true);
      const lho = dv.getUint32(p + 42, true);
      const name = dec.decode(u8.subarray(p + 46, p + 46 + nl));
      p += 46 + nl + el + cl;
      if (name.endsWith('/')) continue;
      entries.set(name, { method, size, lho });
    }
    const slice = (name) => {
      const e = entries.get(name);
      if (!e) return null;
      if (e.method !== 0) throw new Error('圧縮された ZIP には対応していません（無圧縮のみ）');
      if (dv.getUint32(e.lho, true) !== 0x04034b50) throw new Error('ZIP のローカルヘッダが壊れています');
      const start = e.lho + 30 + dv.getUint16(e.lho + 26, true) + dv.getUint16(e.lho + 28, true);
      if (start + e.size > u8.length) throw new Error('ZIP が途中で切れています');
      return u8.subarray(start, start + e.size);
    };
    return {
      names: () => [...entries.keys()],
      has: (n) => entries.has(n),
      bytes: slice,
      text: (n) => { const b = slice(n); return b ? dec.decode(b) : null; },
      json: (n) => { const t = slice(n); return t ? JSON.parse(dec.decode(t)) : null; },
    };
  }

  /* ---------- パックの導入 ---------- */
  function applyPatches(patches, byId) {
    let n = 0;
    for (const p of patches) {
      const q = byId.get(p.id);
      if (!q) continue;
      for (const k of ['explanation_images', 'explanation_source', 'tags', 'difficulty']) if (p[k] !== undefined) q[k] = p[k];
      n++;
    }
    return n;
  }

  async function installPack(src, onProgress) {
    const rep = (r, label) => { if (onProgress) try { onProgress(Math.max(0, Math.min(1, r)), label); } catch (e) { /* noop */ } };
    rep(0, '読み込み中');
    const buf = src instanceof ArrayBuffer || ArrayBuffer.isView(src) ? src : await src.arrayBuffer();
    const zip = readZip(buf);
    const manifest = zip.json('manifest.json');
    if (!manifest || manifest.format !== 'fe-pack' || manifest.version !== 1 || !['public', 'private'].includes(manifest.kind)) {
      throw new Error('FE Study のデータパックではありません');
    }
    const questions = zip.json('questions.json');
    if (!Array.isArray(questions)) throw new Error('questions.json がありません');
    const categories = zip.json('categories.json') || [];
    const patches = zip.json('patches.json') || [];
    const images = zip.names().filter((n) => n.startsWith('images/') && n.length > 7);
    const total = questions.length + images.length + 3;
    let done = 0;

    // 画像（バッチごとにトランザクション。Blob は同期的に作る）
    for (let i = 0; i < images.length; i += 40) {
      const rows = images.slice(i, i + 40).map((n) => ({ path: n.slice(7), blob: new Blob([zip.bytes(n)], { type: 'image/png' }) }));
      await putMany('blobs', rows);
      done += rows.length; rep(done / total, '画像を保存中'); await tick();
    }
    // 問題（patches 適用前に保存。同じ id は上書き）
    for (let i = 0; i < questions.length; i += 200) {
      const rows = questions.slice(i, i + 200);
      await putMany('questions', rows);
      done += rows.length; rep(done / total, '問題を保存中'); await tick();
    }
    // カテゴリ（meta に保存。id で既存とマージ）
    const old = await getOne('meta', 'categories');
    const byCat = new Map(((old && old.rows) || []).map((c) => [c.id, c]));
    for (const c of categories) byCat.set(c.id, c);
    const catRows = [...byCat.values()];
    await putMany('meta', [{ key: 'categories', rows: catRows, value: catRows }]);
    rep((++done) / total, 'カテゴリを保存中');

    // patches: 保存しておき、public 再導入時にも再適用できるようにする
    if (patches.length) {
      const oldp = await getOne('meta', 'patches');
      const m = new Map(((oldp && oldp.patches) || []).map((p) => [p.id, p]));
      for (const p of patches) m.set(p.id, Object.assign({}, m.get(p.id), p));
      await putMany('meta', [{ key: 'patches', patches: [...m.values()] }]);
    }
    const allPatches = ((await getOne('meta', 'patches')) || {}).patches || [];
    let patched = 0;
    if (allPatches.length) {
      const all = await getAll('questions');
      const byId = new Map(all.map((q) => [q.id, q]));
      patched = applyPatches(allPatches, byId);
      await putMany('questions', [...byId.values()].filter((q) => allPatches.some((p) => p.id === q.id)));
    }
    rep((++done) / total, '仕上げ中');
    await putMany('meta', [{ key: 'pack:' + manifest.kind, manifest, installed_at: new Date().toISOString() }]);
    rep(1, '完了');
    if (root.FELocal && typeof root.FELocal.invalidate === 'function') root.FELocal.invalidate();
    return { manifest, questions: questions.length, images: images.length, patched };
  }

  async function packStatus() {
    const db = await openDb();
    const count = await reqP(db.transaction('questions').objectStore('questions').count());
    const out = { question_count: count, public: null, private: null };
    for (const k of ['public', 'private']) {
      const m = await getOne('meta', 'pack:' + k);
      if (m) out[k] = { manifest: m.manifest, installed_at: m.installed_at };
    }
    return out;
  }

  /* ---------- ダウンロード ---------- */
  async function fetchIndex() {
    try {
      const r = await fetch('packs/index.json', { cache: 'no-cache' });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }
  // index.json の形が違っても public の { url, size } を拾う
  function findPublic(idx) {
    let best = null;
    (function walk(o, key) {
      if (!o || typeof o !== 'object') return;
      if (typeof o.size === 'number' || typeof o.bytes === 'number') {
        const f = o.file || o.url || o.name || o.path || '';
        if (/public/.test(String(f) + ' ' + (key || '') + ' ' + (o.kind || ''))) best = best || { file: f, size: o.size || o.bytes, created_at: o.created_at };
      }
      for (const [k, v] of Object.entries(o)) if (v && typeof v === 'object') walk(v, k);
    })(idx, '');
    return best;
  }
  async function publicInfo() {
    const f = findPublic(await fetchIndex());
    return { url: (f && f.file && /\.zip$/.test(f.file)) ? 'packs/' + String(f.file).replace(/^.*\//, '') : 'packs/fe-pack-public.zip', size: f ? f.size : null };
  }

  async function downloadPublicPack(url, onProgress) {
    const rep = (r, label) => { if (onProgress) try { onProgress(Math.max(0, Math.min(1, r)), label); } catch (e) { /* noop */ } };
    const info = await publicInfo();
    url = url || info.url;
    const res = await fetch(url, { cache: 'no-cache' });
    if (!res.ok) throw new Error('ダウンロードに失敗しました（HTTP ' + res.status + '）');
    let total = Number(res.headers.get('content-length')) || info.size || 0;
    let buf;
    if (res.body && res.body.getReader) {
      const rd = res.body.getReader(), chunks = [];
      let got = 0;
      for (;;) {
        const { done, value } = await rd.read();
        if (done) break;
        chunks.push(value); got += value.length;
        rep(total ? Math.min(0.95, got / total) * 0.5 : 0, 'ダウンロード中');
      }
      buf = new Uint8Array(got);
      let o = 0;
      for (const c of chunks) { buf.set(c, o); o += c.length; }
    } else {
      buf = new Uint8Array(await res.arrayBuffer());
    }
    rep(0.5, '保存中');
    return installPack(buf, (r, label) => rep(0.5 + r * 0.5, label));
  }

  async function clearAll() {
    const db = await openDb();
    const tx = db.transaction(STORES.map((s) => s[0]), 'readwrite');
    for (const [n] of STORES) tx.objectStore(n).clear();
    await txDone(tx);
    if (root.FELocal && typeof root.FELocal.invalidate === 'function') root.FELocal.invalidate();
  }

  root.FEPack = { readZip, installPack, packStatus, downloadPublicPack, fetchIndex, publicInfo, clearAll };
})(typeof window !== 'undefined' ? window : globalThis);
