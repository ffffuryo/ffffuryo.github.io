'use strict';
// store.js の IndexedDB 実装を Node で検証するための最小の偽 IndexedDB（このリポジトリのテスト専用）。
// 対応: open(+onupgradeneeded) / createObjectStore / createIndex / transaction / get getAll put delete clear count / index.getAll / abort
const clone = (v) => structuredClone(v);
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

function createFakeIndexedDB() {
  const dbs = new Map(); // name -> {version, stores: {name: {opt, indexes, rows: Map, seq}}}

  function makeRequest() { return { result: undefined, error: null, onsuccess: null, onerror: null }; }

  function makeDb(rec) {
    const db = {
      version: rec.version,
      objectStoreNames: { contains: (n) => n in rec.stores },
      createObjectStore(name, opt) {
        rec.stores[name] = { opt: opt || {}, indexes: {}, rows: new Map(), seq: 0 };
        return { createIndex: (iname, kp) => { rec.stores[name].indexes[iname] = kp; } };
      },
      close() {},
      onversionchange: null,
      transaction(names, mode) {
        names = [].concat(names);
        for (const n of names) if (!(n in rec.stores)) throw new Error('NotFoundError: ' + n);
        const writable = mode === 'readwrite';
        const work = {};
        for (const n of names) {
          const s = rec.stores[n];
          work[n] = { opt: s.opt, indexes: s.indexes, rows: new Map(Array.from(s.rows, ([k, v]) => [k, clone(v)])), seq: s.seq };
        }
        const tx = { oncomplete: null, onabort: null, onerror: null, error: null, pending: 0, done: false };
        const finish = (ok) => {
          if (tx.done) return;
          tx.done = true;
          if (ok) {
            if (writable) for (const n of names) { rec.stores[n].rows = work[n].rows; rec.stores[n].seq = work[n].seq; }
            if (tx.oncomplete) tx.oncomplete({});
          } else if (tx.onabort) tx.onabort({});
        };
        const check = () => setTimeout(() => { if (!tx.done && tx.pending === 0) finish(true); }, 0);
        tx.abort = () => { if (tx.done) throw new Error('InvalidStateError'); tx.done = true; setTimeout(() => { if (tx.onabort) tx.onabort({}); }, 0); };
        const run = (fn) => {
          if (tx.done) throw new Error('TransactionInactiveError');
          const rq = makeRequest();
          tx.pending++;
          let res, err = null;
          try { res = fn(); } catch (e) { err = e; }
          setTimeout(() => {
            if (tx.done) return;
            if (err) { rq.error = err; if (rq.onerror) rq.onerror({}); tx.pending--; return; }
            rq.result = res;
            if (rq.onsuccess) rq.onsuccess({});
            tx.pending--;
            check();
          }, 0);
          return rq;
        };
        tx.objectStore = (n) => {
          if (!names.includes(n)) throw new Error('NotFoundError');
          const s = work[n];
          const sorted = () => Array.from(s.rows.entries()).sort((a, b) => cmp(a[0], b[0])).map((e) => clone(e[1]));
          const w = () => { if (!writable) throw new Error('ReadOnlyError'); };
          return {
            get: (k) => run(() => { const r = s.rows.get(k); return r === undefined ? undefined : clone(r); }),
            getAll: () => run(sorted),
            count: () => run(() => s.rows.size),
            put: (v) => run(() => {
              w();
              const row = clone(v);
              let key = row[s.opt.keyPath];
              if (key === undefined) {
                if (!s.opt.autoIncrement) throw new Error('DataError');
                key = ++s.seq; row[s.opt.keyPath] = key;
              } else if (s.opt.autoIncrement && typeof key === 'number' && key > s.seq) s.seq = Math.floor(key);
              s.rows.set(key, row);
              return key;
            }),
            delete: (k) => run(() => { w(); s.rows.delete(k); }),
            clear: () => run(() => { w(); s.rows.clear(); }),
            index: (iname) => ({ getAll: (k) => run(() => sorted().filter((r) => r[s.indexes[iname]] === k)) }),
          };
        };
        check();
        return tx;
      },
    };
    return db;
  }

  return {
    open(name, version) {
      const rq = makeRequest();
      setTimeout(() => {
        let rec = dbs.get(name);
        const fresh = !rec;
        if (!rec) { rec = { version, stores: {} }; dbs.set(name, rec); }
        rq.result = makeDb(rec);
        if (fresh && rq.onupgradeneeded) rq.onupgradeneeded({});
        if (rq.onsuccess) rq.onsuccess({});
      }, 0);
      return rq;
    },
    deleteDatabase(name) {
      const rq = makeRequest();
      setTimeout(() => { dbs.delete(name); if (rq.onsuccess) rq.onsuccess({}); }, 0);
      return rq;
    },
    _dbs: dbs,
  };
}

module.exports = { createFakeIndexedDB };
