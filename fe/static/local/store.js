/* FE Study — IndexedDB ラッパー（SPEC 7.3）。window.FEStore（Node では module.exports）
 *
 *   const store = await FEStore.open();      // IndexedDB 'fe-study' v1
 *   const store = FEStore.memory();          // 同じインタフェースのメモリ実装（Node テスト用）
 *
 *   await store.tx(['sessions','attempts'], 'readwrite', async (t) => {   // 1 トランザクション（原子的）
 *     const s = await t.get('sessions', 1);
 *     await t.put('attempts', {...});          // autoIncrement 側は採番された key を返す
 *   });
 *   単発の近道: store.get / getAll / getAllByIndex / put / putMany / delete / clear / count
 *
 * tx のコールバック内では t.* 以外の非同期処理（fetch・setTimeout 等）を await しないこと
 * （IndexedDB のトランザクションが自動コミットされる）。コールバックが throw したら abort（全て巻き戻す）。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FEStore = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DB_NAME = 'fe-study', DB_VERSION = 1;
  // [name, options, [[indexName, keyPath], ...]]（pack.js と同一スキーマ）
  const SCHEMA = [
    ['questions', { keyPath: 'id' }, [['part', 'part'], ['pool', 'pool']]],
    ['blobs', { keyPath: 'path' }, []],
    ['sessions', { keyPath: 'id', autoIncrement: true }, [['status', 'status']]],
    ['attempts', { keyPath: 'id', autoIncrement: true }, [['question_id', 'question_id'], ['session_id', 'session_id']]],
    ['settings', { keyPath: 'key' }, []],
    ['meta', { keyPath: 'key' }, []],
  ];
  const STORE_NAMES = SCHEMA.map((s) => s[0]);

  /* ---------- 共通: 単発操作の近道 ---------- */
  function addShortcuts(store) {
    const one = (name, fn) => store.tx([name], fn.rw ? 'readwrite' : 'readonly', (t) => fn(t));
    store.get = (n, k) => one(n, (t) => t.get(n, k));
    store.getAll = (n) => one(n, (t) => t.getAll(n));
    store.getAllByIndex = (n, i, k) => one(n, (t) => t.getAllByIndex(n, i, k));
    store.count = (n) => one(n, (t) => t.count(n));
    const rw = (f) => { f.rw = true; return f; };
    store.put = (n, v) => one(n, rw((t) => t.put(n, v)));
    store.delete = (n, k) => one(n, rw((t) => t.delete(n, k)));
    store.putMany = (n, rows) => one(n, rw(async (t) => { for (const r of rows) await t.put(n, r); }));
    store.clear = (names) => store.tx([].concat(names || STORE_NAMES), 'readwrite', async (t) => {
      for (const n of [].concat(names || STORE_NAMES)) await t.clear(n);
    });
    return store;
  }

  /* ---------- IndexedDB 実装 ---------- */
  const reqP = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });

  function openDb(name) {
    return new Promise((resolve, reject) => {
      const rq = indexedDB.open(name || DB_NAME, DB_VERSION);
      rq.onupgradeneeded = () => {
        const db = rq.result;
        for (const [n, opt, idx] of SCHEMA) {
          if (db.objectStoreNames.contains(n)) continue;
          const os = db.createObjectStore(n, opt);
          for (const [iname, kp] of idx) os.createIndex(iname, kp);
        }
      };
      rq.onsuccess = () => resolve(rq.result);
      rq.onerror = () => reject(rq.error);
      rq.onblocked = () => reject(new Error('データベースが他のタブで使用中です'));
    });
  }

  async function open(name) {
    const db = await openDb(name);
    db.onversionchange = () => db.close();
    const store = {
      kind: 'indexeddb',
      db,
      async tx(names, mode, fn) {
        const tx = db.transaction(names, mode === 'readwrite' ? 'readwrite' : 'readonly');
        const done = new Promise((res, rej) => {
          tx.oncomplete = () => res();
          tx.onabort = () => rej(tx.error || new Error('transaction aborted'));
        });
        done.catch(() => {});
        const os = (n) => tx.objectStore(n);
        const t = {
          get: (n, k) => reqP(os(n).get(k)),
          getAll: (n) => reqP(os(n).getAll()),
          getAllByIndex: (n, i, k) => reqP(os(n).index(i).getAll(k)),
          count: (n) => reqP(os(n).count()),
          put: (n, v) => reqP(os(n).put(v)),
          delete: (n, k) => reqP(os(n).delete(k)),
          clear: (n) => reqP(os(n).clear()),
        };
        let result;
        try {
          result = await fn(t);
        } catch (e) {
          try { tx.abort(); } catch (_) { /* 既に終了 */ }
          await done.catch(() => {});
          throw e;
        }
        await done;
        return result;
      },
      close() { db.close(); },
    };
    return addShortcuts(store);
  }

  /* ---------- メモリ実装（Node テスト・IndexedDB 非対応環境） ---------- */
  function clone(v) {
    if (v === null || typeof v !== 'object') return v;
    if (typeof Blob !== 'undefined' && v instanceof Blob) return v;
    try { return structuredClone(v); } catch (e) { return JSON.parse(JSON.stringify(v)); }
  }
  const cmpKey = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  function memory() {
    const data = {};
    for (const [n, opt, idx] of SCHEMA) {
      data[n] = { keyPath: opt.keyPath, auto: !!opt.autoIncrement, indexes: idx.map((i) => i[0]), rows: new Map(), seq: 0 };
    }
    let chain = Promise.resolve(); // トランザクションは直列実行（IndexedDB の readwrite と同じ排他）
    const sortedRows = (d) => Array.from(d.rows.entries()).sort((a, b) => cmpKey(a[0], b[0])).map((e) => e[1]);

    const store = {
      kind: 'memory',
      tx(names, mode, fn) {
        const run = async () => {
          const writable = mode === 'readwrite';
          const snap = writable ? names.map((n) => [n, new Map(data[n].rows), data[n].seq]) : null;
          const d = (n) => {
            if (!names.includes(n)) throw new Error(`store '${n}' is not in the transaction scope`);
            return data[n];
          };
          const t = {
            async get(n, k) { const r = d(n).rows.get(k); return r === undefined ? undefined : clone(r); },
            async getAll(n) { return sortedRows(d(n)).map(clone); },
            async getAllByIndex(n, i, k) {
              const s = d(n);
              if (!s.indexes.includes(i)) throw new Error(`no index '${i}' on '${n}'`);
              return sortedRows(s).filter((r) => r[i] === k).map(clone);
            },
            async count(n) { return d(n).rows.size; },
            async put(n, v) {
              if (!writable) throw new Error('readonly transaction');
              const s = d(n);
              const row = clone(v);
              let key = row[s.keyPath];
              if (key === undefined || key === null) {
                if (!s.auto) throw new Error(`key '${s.keyPath}' is required`);
                key = ++s.seq;
                row[s.keyPath] = key;
              } else if (s.auto && typeof key === 'number' && key > s.seq) {
                s.seq = Math.floor(key);
              }
              s.rows.set(key, row);
              return key;
            },
            async delete(n, k) { if (!writable) throw new Error('readonly transaction'); d(n).rows.delete(k); },
            async clear(n) { if (!writable) throw new Error('readonly transaction'); d(n).rows.clear(); },
          };
          try {
            return await fn(t);
          } catch (e) {
            if (snap) for (const [n, rows, seq] of snap) { data[n].rows = rows; data[n].seq = seq; }
            throw e;
          }
        };
        const p = chain.then(run);
        chain = p.then(() => undefined, () => undefined);
        return p;
      },
      close() {},
    };
    return addShortcuts(store);
  }

  /** データベースごと削除（「すべて削除」用） */
  function deleteDatabase(name) {
    return new Promise((resolve, reject) => {
      const rq = indexedDB.deleteDatabase(name || DB_NAME);
      rq.onsuccess = () => resolve();
      rq.onerror = () => reject(rq.error);
      rq.onblocked = () => resolve();
    });
  }

  return { DB_NAME, DB_VERSION, SCHEMA, STORE_NAMES, open, memory, deleteDatabase };
});
