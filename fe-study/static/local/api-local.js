/* FE Study — ブラウザ内ローカル API（SPEC 7.4）。window.FELocal（Node では module.exports）
 *
 *   FELocal.api(method, path, body) -> Promise<JSON>   Flask 版 /api と同じレスポンス。
 *     エラーは {status: 400|404|409, error: '日本語メッセージ'}（ApiError）を throw。
 *   FELocal.init({store, rng, now, mediaUrl})           依存注入（テスト用）。省略時:
 *     store    = FEStore.open()（IndexedDB 'fe-study'）
 *     rng      = Math.random
 *     now      = () => ローカル時刻 'YYYY-MM-DD HH:MM:SS'
 *     mediaUrl = async (path) => blob の object URL（パス単位でキャッシュ。無ければ null）
 *   FELocal.invalidate()                                問題データのキャッシュ破棄（パック導入後に呼ぶ）
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./logic.js'), require('./store.js'));
  else root.FELocal = factory(root.FELogic, root.FEStore);
})(typeof self !== 'undefined' ? self : this, function (L, FEStore) {
  'use strict';

  const { ApiError, isInt } = L;
  const pad = (n) => String(n).padStart(2, '0');
  function localNow() {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  const ctx = { store: null, storeP: null, rng: Math.random, now: localNow, mediaUrl: null, qcache: null };
  const blobUrls = new Map();

  async function defaultMediaUrl(path) {
    if (blobUrls.has(path)) return blobUrls.get(path);
    const row = await ctx.store.get('blobs', path);
    if (!row || !row.blob || typeof URL === 'undefined' || !URL.createObjectURL) return null;
    const url = URL.createObjectURL(row.blob);
    blobUrls.set(path, url);
    return url;
  }

  function init(opts) {
    opts = opts || {};
    ctx.store = opts.store || null;
    ctx.storeP = null;
    ctx.rng = opts.rng || Math.random;
    ctx.now = opts.now || localNow;
    ctx.mediaUrl = opts.mediaUrl || defaultMediaUrl;
    ctx.qcache = null;
    blobUrls.clear();
  }
  function invalidate() {
    ctx.qcache = null;
    for (const u of blobUrls.values()) { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } }
    blobUrls.clear();
  }

  async function getStore() {
    if (ctx.store) return ctx.store;
    if (!ctx.storeP) {
      ctx.storeP = FEStore.open().then((s) => { ctx.store = s; return s; }, (e) => { ctx.storeP = null; throw e; });
    }
    return ctx.storeP;
  }
  if (!ctx.mediaUrl) ctx.mediaUrl = defaultMediaUrl;

  /* ---------- 問題データ（キャッシュ。件数と導入済みパックのメタが変わったら読み直す） ---------- */
  async function loadQuestions() {
    const store = await getStore();
    const sig = await store.tx(['questions', 'meta'], 'readonly', async (t) => {
      const n = await t.count('questions');
      const meta = await t.getAll('meta');
      const cats = meta.find((m) => m.key === 'categories');
      L.setCategories(cats ? (cats.rows || cats.value) : null);
      const packs = meta.filter((m) => String(m.key).startsWith('pack:'))
        .map((m) => [m.key, m.installed_at || '', (m.manifest && m.manifest.created_at) || '']);
      return JSON.stringify([n, packs, cats ? (cats.rows || cats.value || []).length : 0]);
    });
    if (ctx.qcache && ctx.qcache.sig === sig) return ctx.qcache;
    const list = await store.getAll('questions');
    ctx.qcache = { sig, list, byId: new Map(list.map((q) => [q.id, q])) };
    return ctx.qcache;
  }

  const settingsObj = (rows) => {
    const o = {};
    for (const r of rows) o[r.key] = r.value;
    return L.mergeSettings(o);
  };

  /* ---------- 入出力 ---------- */
  function bodyObj(body) {
    if (body === undefined || body === null) return {};
    if (typeof body === 'string') { // sendBeacon の text/plain 互換
      if (!body.trim()) return {};
      try { body = JSON.parse(body); } catch (e) { throw new ApiError('JSON オブジェクトが必要です'); }
    }
    if (typeof body !== 'object' || Array.isArray(body)) throw new ApiError('JSON オブジェクトが必要です');
    return body;
  }
  const out = (v) => (v === undefined ? null : JSON.parse(JSON.stringify(v)));
  const notFoundSession = () => new ApiError('セッションが見つかりません', 404);

  /** セッション行 + その attempts → セッションオブジェクト（画像 URL は事前に解決） */
  async function buildSession(s, sessAttempts, qById) {
    const paths = new Set();
    for (const qid of s.question_ids) {
      const q = qById.get(qid);
      if (q) for (const p of L.mediaPathsOf(q)) paths.add(p);
    }
    const urls = new Map();
    await Promise.all(Array.from(paths).map(async (p) => { urls.set(p, (await ctx.mediaUrl(p)) || null); }));
    return L.sessionObject(s, qById, sessAttempts, (p) => urls.get(p) || null);
  }
  async function mediaResolver(paths) {
    const urls = new Map();
    await Promise.all(Array.from(paths).map(async (p) => { urls.set(p, (await ctx.mediaUrl(p)) || null); }));
    return (p) => urls.get(p) || null;
  }

  async function sessionWithAttempts(store, sid) {
    return store.tx(['sessions', 'attempts'], 'readonly', async (t) => {
      const s = await t.get('sessions', sid);
      if (!s) throw notFoundSession();
      return { s, atts: await t.getAllByIndex('attempts', 'session_id', sid) };
    });
  }

  /* ---------- ハンドラ ---------- */
  async function home() {
    const store = await getStore();
    const { list } = await loadQuestions();
    const { sessions, attempts, settings } = await store.tx(['sessions', 'attempts', 'settings'], 'readonly', async (t) => ({
      sessions: await t.getAll('sessions'), attempts: await t.getAll('attempts'), settings: await t.getAll('settings') }));
    return L.homeData(list, sessions, attempts, settingsObj(settings), ctx.now());
  }

  async function createSession(body) {
    const d = bodyObj(body);
    const store = await getStore();
    const { list, byId } = await loadQuestions();
    const sid = await store.tx(['sessions', 'attempts', 'settings'], 'readwrite', async (t) => {
      const settings = settingsObj(await t.getAll('settings'));
      const attempts = await t.getAll('attempts');
      const now = ctx.now();
      const plan = L.planSession(d, settings, list, attempts, ctx.rng, now);
      for (const s of await t.getAllByIndex('sessions', 'status', 'active')) {
        s.status = 'abandoned';
        s.ended_at = now;
        await t.put('sessions', s);
      }
      return t.put('sessions', {
        mode: plan.mode, status: 'active', question_ids: plan.ids, current_index: 0, current_elapsed_ms: 0,
        time_limit_sec: plan.limit, options: plan.opts, started_at: now, updated_at: now, ended_at: null });
    });
    const { s, atts } = await sessionWithAttempts(store, sid);
    return buildSession(s, atts, byId);
  }

  async function activeSession() {
    const store = await getStore();
    const { byId } = await loadQuestions();
    const found = await store.tx(['sessions', 'attempts'], 'readonly', async (t) => {
      let s = null;
      for (const x of await t.getAllByIndex('sessions', 'status', 'active')) if (!s || x.id > s.id) s = x;
      return s ? { s, atts: await t.getAllByIndex('attempts', 'session_id', s.id) } : null;
    });
    return found ? buildSession(found.s, found.atts, byId) : null;
  }

  async function getSession(sid) {
    const store = await getStore();
    const { byId } = await loadQuestions();
    const { s, atts } = await sessionWithAttempts(store, sid);
    return buildSession(s, atts, byId);
  }

  async function answer(sid, body) {
    const store = await getStore();
    const { byId } = await loadQuestions();
    return store.tx(['sessions', 'attempts'], 'readwrite', async (t) => {
      const s = await t.get('sessions', sid);
      if (!s) throw notFoundSession();
      if (s.status !== 'active') throw new ApiError('このセッションは終了しています', 409);
      const d = bodyObj(body);
      const qid = d.question_id, chosen = d.chosen, tm = d.time_ms;
      const ids = s.question_ids;
      if (!ids.includes(qid)) throw new ApiError('このセッションの問題ではありません');
      const q = byId.get(qid);
      if (!q) throw new ApiError('問題が見つかりません', 404);
      if (!(q.part === 'B' ? L.LABELS_B : L.LABELS).includes(chosen)) throw new ApiError('chosen が不正です');
      if (!isInt(tm) || tm < 0) throw new ApiError('time_ms が不正です');
      const ok = chosen === q.answer ? 1 : 0;
      const now = ctx.now();
      const prev = (await t.getAllByIndex('attempts', 'session_id', sid)).find((a) => a.question_id === qid);
      await t.put('attempts', Object.assign({}, prev || { session_id: sid, question_id: qid },
        { chosen, is_correct: ok, time_ms: tm, answered_at: now }));
      s.current_index = Math.max(s.current_index, ids.indexOf(qid) + 1);
      s.current_elapsed_ms = 0;
      s.updated_at = now;
      await t.put('sessions', s);
      if (s.mode === 'mock') return { saved: true };
      const res = { is_correct: !!ok, answer: q.answer, explanation_url: q.explanation_url };
      if (q.part === 'B') {
        const url = await mediaResolver(L.mediaPathsOf(q)); // blob の読み出しは別トランザクションだが読み取りのみ
        Object.assign(res, L.explanationFields(q, url));
      }
      return res;
    });
  }

  async function progress(sid, body) {
    const store = await getStore();
    return store.tx(['sessions'], 'readwrite', async (t) => {
      const s = await t.get('sessions', sid);
      if (!s) throw notFoundSession();
      const d = bodyObj(body);
      const idx = d.current_index, el = d.current_elapsed_ms;
      if (!isInt(idx) || idx < 0 || idx > s.question_ids.length || !isInt(el) || el < 0) {
        throw new ApiError('current_index / current_elapsed_ms が不正です');
      }
      if (s.status === 'active') {
        s.current_index = idx;
        s.current_elapsed_ms = el;
        s.updated_at = ctx.now();
        await t.put('sessions', s);
      }
      return { ok: true };
    });
  }

  async function finish(sid) {
    const store = await getStore();
    const { byId } = await loadQuestions();
    const { s, atts } = await store.tx(['sessions', 'attempts'], 'readwrite', async (t) => {
      const s = await t.get('sessions', sid);
      if (!s) throw notFoundSession();
      if (s.status === 'abandoned') throw new ApiError('このセッションは中断されています', 409);
      let atts = await t.getAllByIndex('attempts', 'session_id', sid);
      if (s.status === 'active') {
        const now = ctx.now();
        const have = new Set(atts.map((a) => a.question_id));
        for (const qid of s.question_ids) {
          if (!have.has(qid)) await t.put('attempts', { session_id: sid, question_id: qid, chosen: null, is_correct: 0, time_ms: 0, answered_at: now });
        }
        s.status = 'done';
        s.ended_at = now;
        s.updated_at = now;
        await t.put('sessions', s);
        atts = await t.getAllByIndex('attempts', 'session_id', sid);
      }
      return { s, atts };
    });
    const obj = await buildSession(s, atts, byId);
    return L.finishResult(obj, s, atts);
  }

  async function settingsGet() {
    const store = await getStore();
    return settingsObj(await store.getAll('settings'));
  }
  async function settingsPut(body) {
    const vals = L.validateSettings(bodyObj(body));
    const store = await getStore();
    await store.tx(['settings'], 'readwrite', async (t) => {
      for (const k of Object.keys(vals)) await t.put('settings', { key: k, value: vals[k] });
    });
    return settingsGet();
  }

  function queryPart(params, allowed, def) {
    const p = params.has('part') ? params.get('part') : def;
    if (!allowed.includes(p)) throw new ApiError(`part は ${allowed.join('/')} です`);
    return p;
  }

  async function readAll() {
    const store = await getStore();
    const { list } = await loadQuestions();
    const r = await store.tx(['sessions', 'attempts', 'settings'], 'readonly', async (t) => ({
      sessions: await t.getAll('sessions'), attempts: await t.getAll('attempts'), settings: await t.getAll('settings') }));
    return { questions: list, sessions: r.sessions, attempts: r.attempts, settings: settingsObj(r.settings) };
  }

  async function history(params) {
    const { limit, offset } = L.parseHistoryParams(params);
    const d = await readAll();
    return L.historyList(d.questions, d.sessions, d.attempts, limit, offset);
  }

  async function questionGet(qid) {
    const store = await getStore();
    const { byId } = await loadQuestions();
    const q = byId.get(qid);
    if (!q) throw new ApiError('問題が見つかりません', 404);
    const { sessions, attempts } = await store.tx(['sessions', 'attempts'], 'readonly', async (t) => ({
      sessions: await t.getAll('sessions'), attempts: await t.getAllByIndex('attempts', 'question_id', qid) }));
    const url = await mediaResolver(L.mediaPathsOf(q));
    return L.questionDetail(q, sessions, attempts, url);
  }

  /* ---------- ルーティング ---------- */
  async function dispatch(method, path, body) {
    method = String(method || 'GET').toUpperCase();
    const qpos = path.indexOf('?');
    const pathname = qpos < 0 ? path : path.slice(0, qpos);
    const params = new URLSearchParams(qpos < 0 ? '' : path.slice(qpos + 1));
    const seg = pathname.split('/').filter((x) => x !== '');
    const notFound = () => new ApiError('見つかりません', 404);
    const allow = (...ms) => { if (!ms.includes(method)) throw new ApiError('このメソッドは使えません', 405); };
    if (seg[0] !== 'api') throw notFound();
    const [, a, b, c] = seg;
    if (a === 'home' && seg.length === 2) { allow('GET'); return home(); }
    if (a === 'settings' && seg.length === 2) {
      allow('GET', 'PUT');
      return method === 'GET' ? settingsGet() : settingsPut(body);
    }
    if (a === 'sessions') {
      if (seg.length === 2) { allow('POST'); return createSession(body); }
      if (b === 'active' && seg.length === 3) { allow('GET'); return activeSession(); }
      if (/^\d+$/.test(b || '')) {
        const sid = parseInt(b, 10);
        if (seg.length === 3) { allow('GET'); return getSession(sid); }
        if (seg.length === 4 && c === 'answer') { allow('POST'); return answer(sid, body); }
        if (seg.length === 4 && c === 'progress') { allow('POST'); return progress(sid, body); }
        if (seg.length === 4 && c === 'finish') { allow('POST'); return finish(sid); }
      }
      throw notFound();
    }
    if (a === 'stats' && seg.length === 3) {
      allow('GET');
      if (b === 'categories') {
        const part = queryPart(params, ['A', 'B'], 'A');
        const d = await readAll();
        return L.categoryStats(d.questions, d.attempts, d.settings, part);
      }
      if (b === 'time') {
        const part = queryPart(params, ['A', 'B', 'all'], 'all');
        const d = await readAll();
        return L.timeStats(d.questions, d.attempts, d.settings, part, L.todayOf(ctx.now()));
      }
      if (b === 'b') {
        const d = await readAll();
        return L.bStats(d.questions, d.attempts);
      }
      throw notFound();
    }
    if (a === 'history' && seg.length === 2) { allow('GET'); return history(params); }
    if (a === 'questions' && seg.length === 3) {
      allow('GET');
      let qid;
      try { qid = decodeURIComponent(b); } catch (e) { throw notFound(); }
      return questionGet(qid);
    }
    throw notFound();
  }

  async function api(method, path, body) {
    return out(await dispatch(method, String(path), body));
  }

  ctx.mediaUrl = defaultMediaUrl;
  return { api, init, invalidate, ApiError, _ctx: ctx };
});
