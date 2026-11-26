/* FE Study — 学習記録（sessions / attempts / settings）のバックアップ。問題データは含めない。
 * window.FEBackup: exportData / download / importData(obj|File, mode 'merge'|'replace') */
(function (root) {
  'use strict';
  const reqP = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const txDone = (tx) => new Promise((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error || new Error('tx aborted')); });
  function openDb() {
    return new Promise((resolve, reject) => {
      const rq = indexedDB.open('fe-study', 1);
      rq.onupgradeneeded = () => { rq.transaction.abort(); reject(new Error('データベースが未初期化です')); };
      rq.onsuccess = () => resolve(rq.result);
      rq.onerror = () => reject(rq.error);
    });
  }
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

  async function exportData() {
    const db = await openDb();
    const tx = db.transaction(['sessions', 'attempts', 'settings']);
    const [sessions, attempts, settings] = await Promise.all(['sessions', 'attempts', 'settings'].map((s) => reqP(tx.objectStore(s).getAll())));
    db.close();
    return { format: 'fe-backup', version: 1, exported_at: stamp(new Date()), sessions, attempts, settings };
  }

  async function download() {
    const data = await exportData();
    const d = new Date();
    const name = `fe-study-backup-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.json`;
    const blob = new Blob([JSON.stringify(data)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name; a.style.display = 'none';
    document.body.appendChild(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 30000);
    return { name, sessions: data.sessions.length, attempts: data.attempts.length };
  }

  const skey = (s) => `${s.mode}|${s.started_at}|${JSON.stringify(s.question_ids)}`;
  const akey = (a, sk) => `${sk}|${a.question_id}`;

  async function importData(src, mode) {
    mode = mode || 'merge';
    let obj = src;
    if (src && typeof src.text === 'function') obj = JSON.parse(await src.text());
    else if (typeof src === 'string') obj = JSON.parse(src);
    if (!obj || obj.format !== 'fe-backup' || !Array.isArray(obj.sessions) || !Array.isArray(obj.attempts)) throw new Error('FE Study のバックアップファイルではありません');
    const db = await openDb();
    const tx = db.transaction(['sessions', 'attempts', 'settings'], 'readwrite');
    const S = tx.objectStore('sessions'), A = tx.objectStore('attempts'), T = tx.objectStore('settings');
    let addedS = 0, addedA = 0, skippedA = 0;
    const work = (async () => {
      if (mode === 'replace') { S.clear(); A.clear(); }
      const curS = mode === 'replace' ? [] : await reqP(S.getAll());
      const curA = mode === 'replace' ? [] : await reqP(A.getAll());
      const keyOfId = new Map(curS.map((s) => [s.id, skey(s)]));
      const idOfKey = new Map(curS.map((s) => [skey(s), s.id]));
      const seenA = new Set(curA.map((a) => akey(a, keyOfId.get(a.session_id))));
      const newIdOf = new Map(); // バックアップ側 session id → 本端末の id
      for (const s of obj.sessions) {
        const k = skey(s);
        let id = idOfKey.get(k);
        if (id == null) {
          const row = Object.assign({}, s); delete row.id;
          id = await reqP(S.add(row));
          idOfKey.set(k, id); addedS++;
        }
        newIdOf.set(s.id, id);
      }
      const srcKey = new Map(obj.sessions.map((s) => [s.id, skey(s)]));
      for (const a of obj.attempts) {
        const sk = srcKey.get(a.session_id);
        if (sk == null || !newIdOf.has(a.session_id)) { skippedA++; continue; }
        const k = akey(a, sk);
        if (seenA.has(k)) { skippedA++; continue; }
        const row = Object.assign({}, a, { session_id: newIdOf.get(a.session_id) }); delete row.id;
        A.add(row); seenA.add(k); addedA++;
      }
      for (const st of obj.settings || []) if (st && st.key != null) T.put(st);
    })();
    try { await work; } catch (e) { try { tx.abort(); } catch (x) { /* noop */ } throw e; }
    await txDone(tx);
    db.close();
    if (root.FELocal && typeof root.FELocal.invalidate === 'function') root.FELocal.invalidate();
    return { sessions_added: addedS, attempts_added: addedA, attempts_skipped: skippedA };
  }

  root.FEBackup = { exportData, download, importData };
})(typeof window !== 'undefined' ? window : globalThis);
