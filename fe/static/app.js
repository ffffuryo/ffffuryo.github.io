/* FE Study — フロントエンド（素の ES2020、ビルド不要） */
'use strict';
(() => {
  /* ================= ユーティリティ ================= */
  const DEFAULTS = { include_calc: true, session_size: 8, category_ids: [], font_scale: 1.0, theme: 'auto', target_sec: 90, session_size_b: 2, target_sec_b: 300, b_topics: [] };
  const MODE_NAME = { study: '学習', review: '復習', mock: '模擬試験' };
  const B_TOPIC_NAME = { pseudo: '擬似言語', security: '情報セキュリティ' };
  const partLabel = (p) => (p === 'B' ? '科目B' : '科目A');
  const imgList = (q) => (q.image_urls && q.image_urls.length ? q.image_urls : q.image_url ? [q.image_url] : []);
  const targetSecOf = (q) => (q && q.part === 'B' ? (S.settings.target_sec_b || 300) : (S.settings.target_sec || 90));
  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $('#view');
  const pad = (n) => String(n).padStart(2, '0');

  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 容量/プライベートモード */ } },
  };
  const SS = {
    get(k, d) { try { const v = sessionStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* noop */ } },
  };

  function add(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      el.append(k.nodeType ? k : String(k));
    }
  }
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    add(el, kids);
    return el;
  }
  const SVGNS = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
    add(el, kids);
    return el;
  }

  const ICONS = {
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    chev: '<path d="M9 6l6 6-6 6"/>',
    back: '<path d="M15 6l-6 6 6 6"/>',
    wifi: '<path d="M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0M12 19.5h0"/>',
    dash: '<path d="M7 12h10"/>',
  };
  function icon(name) {
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('class', 'ic');
    s.setAttribute('aria-hidden', 'true');
    s.innerHTML = ICONS[name]; // 固定文字列のみ
    return s;
  }

  const fmtClock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${pad(s % 60)}`; };
  const fmtDur = (ms) => {
    if (ms == null) return '—';
    const s = Math.round(ms / 1000);
    return s < 60 ? `${s}秒` : `${Math.floor(s / 60)}分${pad(s % 60)}秒`;
  };
  const pct = (r) => (r == null ? '—' : `${Math.round(r * 100)}%`);
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  function parseDate(str) {
    if (!str) return null;
    const s = String(str);
    const d = /[zZ]|[+-]\d\d:?\d\d$/.test(s) ? new Date(s) : new Date(s.replace(' ', 'T')); // サーバはローカル時刻で保存（datetime('now','localtime')）
    return isNaN(d) ? null : d;
  }
  function fmtDateTime(str) {
    const d = parseDate(str);
    return d ? `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
  }

  /* ================= 状態 ================= */
  const S = {
    home: null, homeStale: false,
    settings: { ...DEFAULTS },
    quiz: null,
    result: SS.get('fe.result', null),
    lastStart: SS.get('fe.lastStart', null),
    route: 'home',
    cleanup: null,
    cats: null,
  };

  /* ================= API ================= */
  class NetError extends Error {}
  class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }

  const STANDALONE = !!window.FE_STANDALONE;
  async function api(method, path, body, { timeout = 10000 } = {}) {
    if (STANDALONE) { // ブラウザ内ローカル API（fetch しない）。エラーは {status, error} → ApiError に揃える
      try { return await window.FELocal.api(method, path, body); } catch (e) {
        if (e && e.status != null) throw new ApiError(e.status, e.error || e.message || `HTTP ${e.status}`);
        throw e;
      }
    }
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctl.signal,
      });
    } catch (e) {
      throw new NetError(e && e.message);
    } finally {
      clearTimeout(timer);
    }
    const txt = await res.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch { /* 非 JSON */ }
    if (!res.ok) throw new ApiError(res.status, (data && data.error) || res.statusText || `HTTP ${res.status}`);
    return data;
  }

  /* ---- 未送信キュー（回答のみ） ---- */
  const Q_KEY = 'fe.queue';
  const queue = {
    all() { return LS.get(Q_KEY, []); },
    push(item) {
      const a = this.all().filter((x) => !(x.sid === item.sid && x.body.question_id === item.body.question_id));
      a.push(item); LS.set(Q_KEY, a); updateNet();
    },
    set(a) { LS.set(Q_KEY, a); updateNet(); },
  };
  let flushing = null;
  function flushQueue() {
    if (flushing) return flushing;
    flushing = (async () => {
      let ok = true;
      for (;;) {
        const a = queue.all();
        if (!a.length) break;
        const it = a[0];
        try {
          await api('POST', `/api/sessions/${it.sid}/answer`, it.body, { timeout: 8000 });
        } catch (e) {
          if (e instanceof NetError || (e instanceof ApiError && e.status >= 500)) { ok = false; break; }
          /* 4xx は再送しても通らないので破棄 */
        }
        queue.set(queue.all().slice(1));
      }
      updateNet();
      return ok;
    })().finally(() => { flushing = null; });
    return flushing;
  }
  function netText() {
    if (STANDALONE) return '';
    const n = queue.all().length;
    if (!navigator.onLine) return `オフライン：あとで保存${n ? `（${n}件）` : ''}`;
    return n ? `未送信 ${n}件を再送待ち` : '';
  }
  function updateNet() {
    const t = netText();
    document.querySelectorAll('.netpill').forEach((el) => { el.hidden = !t; el.textContent = t; });
  }
  function netPill() {
    const t = netText();
    return h('span', { class: 'netpill', role: 'status', hidden: !t, text: t });
  }

  /* ================= 設定の反映 ================= */
  function applySettings(s) {
    const r = document.documentElement;
    const fs = Number(s.font_scale) || 1;
    r.style.setProperty('--fscale', fs);
    if (s.theme === 'light' || s.theme === 'dark') r.setAttribute('data-theme', s.theme);
    else r.removeAttribute('data-theme');
    LS.set('fe.ui', { font_scale: fs, theme: s.theme });
    const metas = document.querySelectorAll('meta[name="theme-color"]');
    const colors = s.theme === 'dark' ? ['#1f2124', '#1f2124'] : s.theme === 'light' ? ['#f4f1ea', '#f4f1ea'] : ['#f4f1ea', '#1f2124'];
    metas.forEach((m, i) => { m.setAttribute('content', colors[i] || colors[0]); });
  }

  /* ================= UI 部品 ================= */
  let toastTimer = null;
  function toast(msg, ms = 2200) {
    const t = $('#toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms);
  }

  let sheetToken = 0;
  function openSheet(title, content) {
    const d = $('#sheet');
    d.dataset.token = String(++sheetToken);
    d.replaceChildren(h('div', { class: 'sheet-in' }, h('h2', { id: 'sheet-title', text: title }), content));
    if (!d.open) d.showModal();
    return d;
  }
  function closeSheet() { const d = $('#sheet'); if (d.open) d.close(); }
  function confirmSheet(title, message, okLabel, cancelLabel = 'キャンセル') {
    return new Promise((resolve) => {
      let result = false;
      const d = openSheet(title, [
        h('p', { text: message }),
        h('button', { class: 'btn primary block', type: 'button', onclick: () => { result = true; d.close(); }, text: okLabel }),
        h('button', { class: 'btn block', type: 'button', onclick: () => d.close(), text: cancelLabel }),
      ]);
      const mine = d.dataset.token;
      // 直前のシートの close イベントが遅れて届いても無視する
      d.addEventListener('close', function onClose() {
        if (d.open || d.dataset.token !== mine) return; // 再オープン中に届いた古い close は無視
        d.removeEventListener('close', onClose); resolve(result);
      });
    });
  }

  function renderLoading() { view.replaceChildren(h('div', { class: 'skeleton', text: '読み込み中…' })); }
  function renderError(e, retry) {
    const msg = e instanceof NetError ? 'サーバーに接続できません。電波の良い場所でもう一度お試しください。' : (e && e.message) || 'エラーが発生しました';
    view.replaceChildren(h('div', { class: 'errbox' },
      h('p', { text: msg }),
      h('button', { class: 'btn primary', type: 'button', onclick: retry, text: '再試行' })));
  }

  function hbar(ratio, marker) {
    const svg = sv('svg', { class: 'hbar', viewBox: '0 0 100 10', preserveAspectRatio: 'none', 'aria-hidden': 'true' },
      sv('rect', { class: 'track', x: 0, y: 0, width: 100, height: 10 }),
      sv('rect', { class: 'fill', x: 0, y: 0, width: (clamp01(ratio) * 100).toFixed(2), height: 10 }));
    if (marker != null) svg.append(sv('line', { class: 'mk', x1: clamp01(marker) * 100, x2: clamp01(marker) * 100, y1: 0, y2: 10 }));
    return svg;
  }
  const mark = (ok) => (ok === true ? icon('check') : ok === false ? icon('x') : icon('dash'));

  /* ================= ルーティング ================= */
  let routeGen = 0;
  const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, '').split('?')[0];
    const parts = raw.split('/').filter(Boolean).map(decodeURIComponent);
    return { name: parts[0] || 'home', args: parts.slice(1) };
  }
  const TAB_OF = { home: 'home', result: 'home', quiz: 'home', stats: 'stats', history: 'history', q: 'history', settings: 'settings' };

  async function route() {
    if (S.cleanup) { const c = S.cleanup; S.cleanup = null; c(); }
    const gen = ++routeGen;
    const { name, args } = parseHash();
    const routes = { home: viewHome, quiz: viewQuiz, result: viewResult, stats: viewStats, history: viewHistory, settings: viewSettings, q: viewQuestion };
    const fn = routes[name] || viewHome;
    S.route = routes[name] ? name : 'home';
    document.body.dataset.route = S.route;
    document.querySelectorAll('#tabbar a').forEach((a) => {
      if (a.dataset.tab === TAB_OF[S.route]) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    closeSheet();
    window.scrollTo(0, 0);
    const ctx = { alive: () => gen === routeGen };
    try { await fn(ctx, args); } catch (e) { if (ctx.alive()) renderError(e, () => route()); }
  }

  /* ================= ホーム ================= */
  async function viewHome(ctx) {
    if (S.home && !S.homeStale) renderHome();
    else {
      if (!S.home) renderLoading();
      try {
        const home = await api('GET', '/api/home');
        S.home = home; S.homeStale = false;
        S.settings = { ...DEFAULTS, ...home.settings }; applySettings(S.settings);
        flushQueue();
      } catch (e) {
        if (!ctx.alive()) return;
        if (!S.home) return renderError(e, () => route());
      }
      if (!ctx.alive()) return;
      renderHome();
    }
  }

  function renderHome() {
    const hm = S.home, act = hm.active_session, pool = hm.pool || {}, poolB = hm.pool_b || {}, st = S.settings;
    const size = st.session_size, sizeB = st.session_size_b || 2;
    const noQ = (pool.study || 0) === 0 && !act;
    const noB = !(poolB.study > 0);
    const startA = h('button', {
      class: act ? 'btn hero2' : 'btn primary hero', type: 'button', disabled: (pool.study || 0) === 0,
      onclick: () => startSession({ mode: 'study' }),
    },
      h('span', { text: act ? '科目A を解く' : '学習を始める（科目A）' }),
      h('span', { class: 'hero-sub', text: `${size}問・約${size}分` }));
    const startB = h('button', {
      class: act ? 'btn hero2' : 'btn primary hero b', type: 'button', disabled: noB,
      onclick: () => startSession({ mode: 'study', part: 'B' }),
    },
      h('span', { text: act ? '科目B を解く' : '科目B を解く' }),
      h('span', { class: 'hero-sub', text: noB ? '科目B の問題が未取り込み' : `${sizeB}問・約${sizeB * 5}分` }));
    const resume = act ? h('button', { class: 'btn primary hero', type: 'button', onclick: resumeSession },
      h('span', { text: `${act.part === 'B' ? '科目B ' : ''}続きから（${Math.min(act.current_index + 1, act.total)}/${act.total}）` }),
      h('span', { class: 'hero-sub', text: `${partLabel(act.part)}・${MODE_NAME[act.mode] || ''}・残り${Math.max(0, act.total - act.current_index)}問` })) : null;

    const t = hm.today || { answered: 0, correct: 0, time_ms: 0 };
    const tiles = h('div', { class: 'tiles' },
      h('div', { class: 'tile' }, h('b', { text: String(t.answered) }), h('span', { text: '今日の回答数' })),
      h('div', { class: 'tile' }, h('b', { text: t.answered ? `${Math.round((t.correct / t.answered) * 100)}%` : '—' }), h('span', { text: '今日の正答率' })),
      h('div', { class: 'tile' }, h('b', { text: `${hm.streak_days || 0}日` }), h('span', { text: '連続学習' })));

    const weak = (hm.weak_categories || []).map((c) => h('li', null,
      h('button', { class: 'rowbtn', type: 'button', onclick: () => startSession({ mode: 'study', category_ids: [c.id] }) },
        h('span', { class: 'grow', text: c.name }),
        h('span', { class: 'pill', text: `正答率 ${pct(c.accuracy)}` }),
        h('span', { class: 'chev' }, icon('chev')))));

    const hasMockB = (hm.mock_exams || []).some((e) => e.part === 'B') || (poolB.mock || 0) > 0;
    view.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'pagehead' }, h('h1', { text: 'FE Study' }), netPill()),
      resume,
      act ? h('div', { class: 'row2 starts' }, startA, startB) : [startA, startB],
      noQ && h('p', { class: 'muted small', text: '出題できる問題がありません。問題インポーターを実行してください。' }),
      h('div', { class: 'row2' },
        h('button', { class: 'btn', type: 'button', disabled: !pool.review, onclick: () => startSession({ mode: 'review' }) },
          h('span', { text: `科目A 復習（${pool.review || 0}）` }), h('small', { text: '間違えた問題' })),
        h('button', { class: 'btn', type: 'button', disabled: !poolB.review, onclick: () => startSession({ mode: 'review', part: 'B' }) },
          h('span', { text: `科目B 復習（${poolB.review || 0}）` }), h('small', { text: '間違えた問題' }))),
      h('button', { class: 'btn block stack', type: 'button', disabled: !pool.mock && !hasMockB, onclick: openMockSheet },
        h('span', { text: '模擬試験' }), h('small', { class: 'muted', text: '令和5〜8年（科目A・科目B）' })),
      h('section', { class: 'card', 'aria-label': '今日の学習' },
        h('div', { class: 'section-title', text: '今日' }), tiles,
        t.time_ms ? h('p', { class: 'muted small center', style: { marginTop: '6px' }, text: `学習時間 ${fmtDur(t.time_ms)}` }) : null),
      weak.length ? h('section', null, h('div', { class: 'section-title', text: '苦手分野（タップで学習）' }), h('ul', { class: 'list' }, weak)) : null,
      h('p', { class: 'muted small center', text: `科目A プール ${pool.study || 0}問（未学習 ${pool.study_unseen || 0}）${poolB.study ? ` ／ 科目B ${poolB.study}問（未学習 ${poolB.study_unseen || 0}）` : ''}` })));
  }

  function openMockSheet() {
    const all = (S.home && S.home.mock_exams) || [];
    const exA = all.filter((e) => e.part !== 'B'), exB = all.filter((e) => e.part === 'B');
    const btn = (label, sub, body) => h('button', { class: 'rowbtn', type: 'button', onclick: () => { closeSheet(); startSession(body); } },
      h('span', { class: 'grow', text: label }), h('span', { class: 'muted small', text: sub }), h('span', { class: 'chev' }, icon('chev')));
    const pb = (S.home && S.home.pool_b) || {};
    openSheet('模擬試験', [
      h('p', { class: 'muted small', text: '終了まで正誤は表示されません。合格目安は60%です。制限時間は科目Aが1問90秒、科目Bが1問5分です。' }),
      h('div', { class: 'section-title', text: '科目A' }),
      btn('ランダム20問', '約30分', { mode: 'mock', size: 20 }),
      ...exA.map((e) => btn(e.exam_label, `${e.count != null ? `${e.count}問・` : ''}約${Math.round(((e.count || 60) * 90) / 60)}分`, { mode: 'mock', exam_code: e.exam_code })),
      (exB.length || pb.mock) ? h('div', { class: 'section-title', text: '科目B' }) : null,
      (exB.length || pb.mock) ? btn('ランダム（全問から）', '最大12問', { mode: 'mock', part: 'B' }) : null,
      ...exB.map((e) => btn(e.exam_label, `${e.count != null ? `${e.count}問・` : ''}約${(e.count || 6) * 5}分`, { mode: 'mock', part: 'B', exam_code: e.exam_code })),
      h('button', { class: 'btn block', type: 'button', onclick: closeSheet, text: '閉じる' }),
    ]);
  }

  /* ---- セッション開始・再開 ---- */
  async function startSession(body) {
    if (S.home && S.home.active_session) {
      const ok = await confirmSheet('新しく始めますか？', '進行中のセッションは中断扱いになります。', '新しく始める');
      if (!ok) return;
    }
    try {
      const s = await api('POST', '/api/sessions', body);
      S.lastStart = body; SS.set('fe.lastStart', body);
      S.quiz = initQuiz(s);
      S.homeStale = true;
      go('#/quiz');
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) toast('出題できる問題がありません');
      else toast(e instanceof NetError ? 'オフラインのため開始できません' : e.message);
    }
  }
  async function resumeSession() {
    await flushQueue();
    try {
      const s = await api('GET', '/api/sessions/active');
      if (!s) { S.homeStale = true; toast('続きのセッションはありません'); return route(); }
      S.quiz = initQuiz(s); go('#/quiz');
    } catch (e) { toast(e instanceof NetError ? 'オフラインのため再開できません' : e.message); }
  }

  /* ================= 出題 ================= */
  function initQuiz(s) {
    const mock = s.mode === 'mock';
    const rem = s.remaining_sec != null ? s.remaining_sec : s.time_limit_sec;
    return {
      s, idx: s.current_index || 0, base: s.current_elapsed_ms || 0, shownAt: performance.now(), frozen: null,
      deadline: mock && rem != null ? Date.now() + rem * 1000 : null,
      busy: false, done: false, chain: Promise.resolve(), timers: [],
    };
  }
  const curQ = () => S.quiz.s.questions[S.quiz.idx];
  function elapsed() {
    const Q = S.quiz;
    if (Q.frozen != null) return Q.frozen;
    return Q.base + (Q.shownAt != null ? performance.now() - Q.shownAt : 0);
  }

  async function viewQuiz(ctx) {
    if (!S.quiz) {
      renderLoading();
      await flushQueue();
      const s = await api('GET', '/api/sessions/active');
      if (!ctx.alive()) return;
      if (!s) { S.homeStale = true; return go('#/home'); }
      S.quiz = initQuiz(s);
    }
    const Q = S.quiz;
    S.cleanup = () => {
      Q.timers.forEach(clearInterval); Q.timers = [];
      if (S.quiz === Q && !Q.done) sendProgress(false);
      if (S.quiz === Q) S.quiz = null;
      S.homeStale = true;
    };
    if (Q.idx >= Q.s.questions.length) return finishQuiz();
    renderQuiz();
    Q.timers.push(setInterval(tick, 500));
    Q.timers.push(setInterval(() => { if (!document.hidden) sendProgress(false); }, 5000));
  }

  function renderQuiz() {
    const Q = S.quiz, s = Q.s, total = s.questions.length, q = curQ(), mock = s.mode === 'mock';
    Q.busy = false;
    const allEmpty = q.choices.every((c) => !c.text);
    if (!mock && q.attempt) Q.frozen = q.attempt.time_ms; else Q.frozen = null;
    const answeredNow = !mock && !!q.attempt;
    const progress = (Q.idx + (answeredNow ? 1 : 0)) / total;

    const isB = q.part === 'B';
    const header = h('header', { class: 'qhead' },
      h('div', { class: 'qrow' },
        h('button', { class: 'btn ghost', type: 'button', onclick: onAbort }, icon('x'), '中断'),
        h('div', { class: 'qcount', 'aria-label': `${Q.idx + 1}問目 全${total}問`, text: `${Q.idx + 1} / ${total}` }),
        h('div', { class: 'qtimer', id: 'qtimer', role: 'timer', 'aria-label': mock ? '残り時間' : '経過時間', text: '0:00' })),
      h('div', { class: 'bar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': Q.idx, 'aria-label': '進捗' },
        h('i', { style: { width: `${(progress * 100).toFixed(1)}%` } })),
      h('div', { class: 'qmeta' },
        isB ? h('span', { class: 'chip', text: '科目B' }) : null,
        h('span', { class: 'qcat', text: q.category_name }),
        q.sub_label ? h('span', { class: 'qsub', text: q.sub_label }) : null,
        q.is_calc ? h('span', { class: 'chip', text: '計算' }) : null,
        mock ? h('span', { class: 'chip', text: '模試' }) : null,
        netPill()));

    const follow = isB && Q.idx > 0 && q.group_id && Q.s.questions[Q.idx - 1].group_id === q.group_id;
    const main = h('article', { class: 'qmain' },
      // 科目B の body は検索用の抽出テキスト（擬似言語のインデントが崩れる）なので、画像があれば表示しない
      q.body && !(isB && (q.image_urls || []).length) ? h('p', { class: 'qbody', text: q.body }) : null,
      isB ? bImages(q, follow) : [
        q.image_url ? h('button', { class: 'figure', type: 'button', 'aria-label': '図を拡大して表示', onclick: () => openViewer(q.image_url) },
          h('img', { src: q.image_url, alt: '問題の図・表' })) : null,
        q.image_url ? h('div', { class: 'fig-hint', text: 'タップで拡大（ピンチで拡大縮小）' }) : null],
      h('p', { class: 'qsource', text: q.source_label || '' }));

    const choices = h('div', { class: `choices${allEmpty ? ' grid' : ''}${isB ? ' two' : ''}`, role: 'group', 'aria-label': '選択肢' },
      q.choices.map((c) => h('button', {
        class: 'choice', type: 'button', 'data-label': c.label, 'aria-label': c.text ? `${c.label}：${c.text}` : `選択肢 ${c.label}`,
        onclick: () => onPick(c.label),
      }, h('span', { class: 'clabel', text: c.label }), c.text ? h('span', { class: 'ctext', text: c.text }) : null, h('span', { class: 'cmark' }))));

    view.replaceChildren(h('div', { class: 'quiz' },
      header, main, choices,
      h('div', { class: 'feedback', id: 'feedback', role: 'status', 'aria-live': 'polite' }),
      h('div', { class: 'qpad' }),
      h('div', { class: 'actionbar', id: 'actionbar' })));
    paintAnswer();
    tick();
  }

  // 科目B の問題画像。同一グループの2問目以降は共通の問題文（_stem_）を折りたたむ
  function figBtn(url, i, n, all) {
    return h('button', { class: 'figure bfig', type: 'button', 'aria-label': `問題画像 ${i + 1}/${n} を拡大して表示`, onclick: () => openViewer(all || [url], { alt: '問題画像' }) },
      h('img', { src: url, alt: `問題画像 ${i + 1}/${n}`, loading: 'lazy' }));
  }
  function bImages(q, follow) {
    const urls = imgList(q);
    if (!urls.length) return null;
    const stems = follow ? urls.filter((u) => /_stem_/.test(u)) : [];
    const own = follow && stems.length ? urls.filter((u) => !/_stem_/.test(u)) : urls;
    const out = [];
    if (stems.length) {
      const box = h('div', { class: 'stembox', hidden: true }, stems.map((u, i) => figBtn(u, i, stems.length, stems)));
      const t = h('button', { class: 'stembtn', type: 'button', 'aria-expanded': 'false' },
        h('span', { class: 'grow', text: '共通の問題文（前の設問と同じ）— タップで表示' }), h('span', { class: 'chev' }, icon('chev')));
      t.addEventListener('click', () => {
        const open = box.hidden; box.hidden = !open;
        t.setAttribute('aria-expanded', String(open)); t.classList.toggle('open', open);
      });
      out.push(t, box);
    }
    own.forEach((u, i) => out.push(figBtn(u, i, own.length, own)));
    out.push(h('div', { class: 'fig-hint', text: 'タップで拡大（ピンチで拡大縮小）' }));
    return out;
  }
  // 書籍解説ビューア（回答後・結果・単問詳細から）
  let explSeq = 0;
  async function openExplanation(qid, urls, source, note) {
    const seq = ++explSeq; // 取得待ちの間に別の解説ボタンが押されたら、古い方は開かない
    if (!urls || !urls.length) {
      try {
        const d = await api('GET', `/api/questions/${encodeURIComponent(qid)}`);
        if (seq !== explSeq) return;
        if (d.id != null && d.id !== qid) return; // 別の問題の応答は使わない
        urls = d.explanation_image_urls; source = d.explanation_source;
      } catch (e) { toast(e instanceof NetError ? 'オフラインのため解説を取得できません' : e.message); return; }
    }
    if (!urls || !urls.length) { toast('この問題の解説画像はありません'); return; }
    // 複数設問の問は「問全体の解説」を共有するため、どの小問の解説から始まっても正しい旨を明示する
    const title = (source || '解説（書籍付録）') + (note ? `｜${note}` : '');
    openViewer(urls, { title, alt: '解説' });
  }
  const sharedNote = (q) => (q && q.group_id && q.sub_label
    ? `この解説は問全体（全設問共通）です。${q.sub_label} は解説中の該当する設問の見出しを参照` : null);
  const explBtn = (qid, urls, source, has, q) => ((urls && urls.length) || has
    ? h('button', { class: 'btn link', type: 'button', onclick: () => openExplanation(qid, urls, source, sharedNote(q)),
      text: q && q.group_id && q.sub_label ? '解説を見る（書籍付録・問全体）' : '解説を見る（書籍付録）' }) : null);

  function tick() {
    const Q = S.quiz;
    const el = $('#qtimer');
    if (!Q || !el || !curQ()) return;
    if (Q.s.mode === 'mock' && Q.deadline != null) {
      const rem = Math.max(0, Math.ceil((Q.deadline - Date.now()) / 1000));
      el.textContent = fmtClock(rem * 1000);
      el.classList.toggle('over', rem < 300);
      if (rem <= 0 && !Q.busy && !Q.done) { toast('制限時間が終了しました'); finishQuiz(); }
    } else {
      const e = elapsed();
      el.textContent = fmtClock(e);
      el.classList.toggle('over', e > targetSecOf(curQ()) * 1000);
    }
  }

  function paintAnswer() {
    const Q = S.quiz, s = Q.s, q = curQ(), att = q.attempt, mock = s.mode === 'mock';
    const last = Q.idx + 1 >= s.questions.length;
    const fb = $('#feedback'), bar = $('#actionbar');
    fb.replaceChildren(); bar.replaceChildren();
    document.querySelectorAll('.choice').forEach((b) => {
      const lab = b.dataset.label, m = $('.cmark', b);
      b.className = 'choice'; m.replaceChildren(); b.removeAttribute('aria-pressed');
      if (!att) return;
      if (mock || att.pending) {
        const on = att.chosen === lab;
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
        if (on) { b.classList.add('picked'); m.append(icon('check'), att.pending ? '保存待ち' : '選択中'); }
        if (!mock) b.classList.add('locked');
        return;
      }
      b.classList.add('locked');
      if (lab === att.answer) { b.classList.add('correct'); m.append(icon('check'), '正解'); }
      else if (lab === att.chosen) { b.classList.add('wrong'); m.append(icon('x'), 'あなたの回答'); }
      else b.classList.add('dim');
    });
    if (mock) {
      bar.append(h('button', { class: att ? 'btn primary' : 'btn block', type: 'button', onclick: goNext,
        text: att ? (last ? '採点する' : '次へ') : (last ? '答えずに採点する' : '答えずに次へ') }));
      return;
    }
    if (!att) return;

    {
      const book = explBtn(q.id, att.explanation_image_urls, att.explanation_source, q.has_book_explanation, q);
      const link = q.explanation_url ? h('a', { class: 'btn link', href: q.explanation_url, target: '_blank', rel: 'noopener', text: '過去問道場で解説を見る ↗' }) : null;
      if (att.pending) {
        fb.append(h('div', { class: 'fb pending' },
          h('div', { class: 'fb-head' }, icon('wifi'), 'オフライン：あとで保存'),
          h('p', { class: 'small', text: `回答（${att.chosen}）を端末に保存しました。電波が戻ると自動で送信され、正誤は履歴で確認できます。` })));
      } else {
        const ok = att.is_correct;
        fb.append(h('div', { class: `fb ${ok ? 'ok' : 'ng'}` },
          h('div', { class: 'fb-head' }, icon(ok ? 'check' : 'x'), ok ? '正解' : `不正解（正解：${att.answer}）`),
          h('p', { class: 'small muted', text: `所要時間 ${fmtDur(att.time_ms)}（目標 ${fmtDur(targetSecOf(q) * 1000)}）` }),
          book, link));
      }
      bar.append(h('button', { class: 'btn primary', type: 'button', onclick: goNext, text: last ? '結果を見る' : '次へ' }));
      setTimeout(() => fb.scrollIntoView({ block: 'end', behavior: 'smooth' }), 30);
    }
  }

  async function submitAnswer(sid, body) {
    if (STANDALONE) return api('POST', `/api/sessions/${sid}/answer`, body);
    if (queue.all().length) {
      await flushQueue();
      if (queue.all().length) { queue.push({ sid, body, ts: Date.now() }); return null; }
    }
    try {
      return await api('POST', `/api/sessions/${sid}/answer`, body, { timeout: 8000 });
    } catch (e) {
      if (e instanceof NetError || (e instanceof ApiError && e.status >= 500)) { queue.push({ sid, body, ts: Date.now() }); return null; }
      throw e;
    }
  }

  async function onPick(label) {
    const Q = S.quiz;
    if (!Q || Q.busy || Q.done) return;
    const q = curQ(), mock = Q.s.mode === 'mock';
    if (!mock && q.attempt) return;
    const t = Math.round(elapsed());
    const body = { question_id: q.id, chosen: label, time_ms: t };
    const sid = Q.s.id;
    if (mock) {
      q.attempt = { chosen: label, time_ms: t };
      paintAnswer();
      Q.chain = Q.chain.then(() => submitAnswer(sid, body)).catch((e) => toast(`保存できませんでした：${e.message}`));
      return;
    }
    Q.busy = true; Q.frozen = t;
    document.querySelectorAll('.choice').forEach((b) => b.classList.add('locked'));
    try {
      const res = await submitAnswer(sid, body);
      if (S.quiz !== Q) return;
      q.attempt = res
        ? { chosen: label, time_ms: t, is_correct: res.is_correct, answer: res.answer,
            explanation_image_urls: res.explanation_image_urls || (res.attempt && res.attempt.explanation_image_urls) || null,
            explanation_source: res.explanation_source || (res.attempt && res.attempt.explanation_source) || null }
        : { chosen: label, time_ms: t, pending: true };
      if (res && res.explanation_url) q.explanation_url = res.explanation_url;
    } catch (e) {
      Q.frozen = null; Q.busy = false;
      document.querySelectorAll('.choice').forEach((b) => b.classList.remove('locked'));
      toast(`回答を保存できませんでした：${e.message}`);
      return;
    }
    Q.busy = false;
    paintAnswer(); tick();
  }

  function goNext() {
    const Q = S.quiz;
    if (!Q || Q.busy || Q.done) return;
    if (Q.idx + 1 >= Q.s.questions.length) { Q.idx = Q.s.questions.length; finishQuiz(); return; }
    Q.idx += 1; Q.base = 0; Q.shownAt = performance.now(); Q.frozen = null;
    renderQuiz(); window.scrollTo(0, 0);
    sendProgress(false);
  }

  function progressPayload() {
    const Q = S.quiz, q = curQ();
    if (Q.s.mode !== 'mock' && q && q.attempt) return { current_index: Q.idx + 1, current_elapsed_ms: 0 };
    return { current_index: Q.idx, current_elapsed_ms: Math.round(elapsed()) };
  }
  function sendProgress(beacon) {
    const Q = S.quiz;
    if (!Q || Q.done || Q.idx >= Q.s.questions.length) return;
    if (STANDALONE) { api('POST', `/api/sessions/${Q.s.id}/progress`, progressPayload()).catch(() => {}); return; }
    if (queue.all().length) { if (!beacon) flushQueue(); return; }
    const url = `/api/sessions/${Q.s.id}/progress`, p = progressPayload();
    if (beacon && navigator.sendBeacon) {
      try { if (navigator.sendBeacon(url, new Blob([JSON.stringify(p)], { type: 'text/plain' }))) return; } catch { /* fallthrough */ }
    }
    api('POST', url, p, { timeout: 6000 }).catch(() => {});
  }

  async function onAbort() {
    const ok = await confirmSheet('中断しますか？', '進み具合は保存され、ホームの「続きから」で再開できます。', '中断してホームへ', '続ける');
    if (ok) go('#/home');
  }

  async function finishQuiz() {
    const Q = S.quiz;
    if (!Q || Q.done) return;
    Q.busy = true;
    Q.timers.forEach(clearInterval); Q.timers = [];
    view.replaceChildren(h('div', { class: 'skeleton', text: '採点しています…' }));
    await Q.chain.catch(() => {});
    await flushQueue();
    try {
      const res = await api('POST', `/api/sessions/${Q.s.id}/finish`, {}, { timeout: 15000 });
      Q.done = true;
      S.result = { ...res, mode: Q.s.mode, session_id: Q.s.id, part: Q.s.part || 'A' };
      SS.set('fe.result', S.result);
      S.quiz = null; S.homeStale = true;
      go('#/result');
    } catch (e) {
      Q.busy = false;
      view.replaceChildren(h('div', { class: 'errbox' },
        h('p', { text: e instanceof NetError ? 'オフラインのため結果を取得できません。回答は端末に保存されています。電波が戻ってから再試行してください。' : e.message }),
        netPill(),
        h('button', { class: 'btn primary', type: 'button', onclick: finishQuiz, text: '再試行' }),
        h('button', { class: 'btn', type: 'button', onclick: () => { Q.idx = Math.min(Q.idx, Q.s.questions.length - 1); go('#/home'); }, text: 'ホームへ（あとで続き）' })));
      Q.timers.push(setInterval(updateNet, 1000));
    }
  }

  /* ---- 画像ビューア（複数画像を縦に並べて表示） ---- */
  function openViewer(src, opts = {}) {
    const v = $('#viewer'), box = $('#viewer-imgs'), sc = $('#viewer-scroll');
    const urls = Array.isArray(src) ? src : [src];
    box.replaceChildren(...urls.map((u, i) => {
      const im = h('img', { src: u, alt: `${opts.alt || '問題の図（拡大）'} ${i + 1}/${urls.length}` });
      im.addEventListener('click', () => sc.classList.toggle('zoom'));
      return im;
    }));
    sc.classList.remove('zoom');
    const ti = $('#viewer-title');
    ti.textContent = opts.title || ''; ti.hidden = !opts.title;
    $('#viewer').setAttribute('aria-label', opts.title ? '解説の表示' : '図の拡大表示');
    v.hidden = false; document.body.classList.add('noscroll');
    sc.scrollTop = 0; // 表示後に先頭へ（非表示中は効かないため）
    history.pushState({ viewer: 1 }, '', location.href);
    $('#viewer-close').focus();
  }
  function hideViewer() {
    const v = $('#viewer');
    if (v.hidden) return;
    v.hidden = true; document.body.classList.remove('noscroll');
    $('#viewer-imgs').replaceChildren();
  }
  function closeViewer() {
    if (history.state && history.state.viewer) history.back(); else hideViewer();
  }
  $('#viewer-close').addEventListener('click', closeViewer);
  $('#viewer-zoom').addEventListener('click', () => $('#viewer-scroll').classList.toggle('zoom'));
  window.addEventListener('popstate', hideViewer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#viewer').hidden) closeViewer(); });

  /* ---- ライフサイクル（オートセーブ） ---- */
  const inQuiz = () => S.route === 'quiz' && S.quiz && !S.quiz.done;
  document.addEventListener('visibilitychange', () => {
    if (!inQuiz()) return;
    const Q = S.quiz;
    if (document.hidden) {
      if (Q.shownAt != null && Q.frozen == null) { Q.base += performance.now() - Q.shownAt; Q.shownAt = null; }
      sendProgress(true);
    } else {
      if (Q.shownAt == null && Q.frozen == null) Q.shownAt = performance.now();
      tick(); flushQueue();
    }
  });
  window.addEventListener('pagehide', () => { if (inQuiz()) sendProgress(true); });
  window.addEventListener('online', () => { updateNet(); flushQueue(); if (Object.keys(pendingPatch).length) flushSettings(); });
  window.addEventListener('offline', updateNet);

  /* ================= 結果 ================= */
  // finish の questions 要素は attempt 入り（または平坦）のどちらでも読めるようにする
  function qres(q) {
    const a = q.attempt || {};
    return {
      chosen: a.chosen ?? q.chosen ?? null,
      correct: a.is_correct ?? q.is_correct ?? null,
      time: a.time_ms ?? q.time_ms ?? 0,
      answer: a.answer ?? q.answer ?? null,
      expl: a.explanation_image_urls ?? q.explanation_image_urls ?? null,
      explSrc: a.explanation_source ?? q.explanation_source ?? null,
    };
  }

  function viewResult() {
    const r = S.result;
    if (!r) return go('#/home');
    const rate = r.total ? r.correct / r.total : 0;
    const mock = r.mode === 'mock';
    let verdict = null;
    if (r.passed != null || mock) {
      const need = Math.max(0, Math.ceil(r.total * 0.6) - r.correct);
      verdict = r.passed
        ? h('div', { class: 'verdict ok' }, icon('check'), '合格目安（60%）に到達')
        : h('div', { class: 'verdict ng' }, icon('x'), `合格目安（60%）まであと${need}問`);
    }
    const cats = (r.by_category || []).map((c) => h('div', null,
      h('div', { class: 'line' }, h('span', { text: c.name }), h('span', { text: `${c.correct} / ${c.total}` })),
      hbar(c.total ? c.correct / c.total : 0)));
    const rows = (r.questions || []).map((q, i) => {
      const x = qres(q);
      const cls = x.chosen == null ? '' : x.correct ? 'ok' : 'ng';
      const book = explBtn(q.id, x.expl, x.explSrc, q.has_book_explanation, q);
      if (book) book.classList.add('small');
      return h('li', { class: book ? 'withexp' : null }, h('button', { class: `qrow2 ${cls}`, type: 'button', onclick: () => go(`#/q/${encodeURIComponent(q.id)}`) },
        h('span', { class: 'mk', 'aria-label': x.chosen == null ? '未回答' : x.correct ? '正解' : '不正解' }, x.chosen == null ? icon('dash') : icon(x.correct ? 'check' : 'x')),
        h('span', { class: 'grow' }, h('span', { text: `${i + 1}. ${q.exam_label || ''} 問${q.number ?? ''}${q.sub_label ? ` ${q.sub_label}` : ''}` }), h('br'), h('span', { class: 'small muted', text: q.category_name || '' })),
        h('span', { class: 't' }, x.chosen == null ? '未回答' : [`${x.chosen}`, h('br'), fmtDur(x.time)])), book);
    });
    view.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'pagehead' }, h('h1', { text: `${r.part === 'B' ? '科目B ' : ''}${MODE_NAME[r.mode] || ''}の結果` }), netPill()),
      h('section', { class: 'card score' },
        h('div', { class: 'big' }, `${r.correct}`, h('small', { text: ` / ${r.total} 問正解` })),
        h('div', { class: 'muted', text: `正答率 ${pct(rate)}` }),
        verdict,
        h('div', { class: 'kv' },
          h('div', null, h('b', { text: fmtDur(r.time_ms) }), '合計時間'),
          h('div', null, h('b', { text: fmtDur(r.avg_time_ms) }), '平均時間'))),
      cats.length ? h('section', null, h('div', { class: 'section-title', text: '分野別' }), h('div', { class: 'card bycat' }, cats)) : null,
      h('section', null, h('div', { class: 'section-title', text: '各問の結果（タップで詳細）' }), h('ul', { class: 'list' }, rows)),
      h('div', { class: 'stickybar' },
        h('button', { class: 'btn primary', type: 'button', onclick: again, text: 'もう1セット' }),
        h('button', { class: 'btn', type: 'button', onclick: () => go('#/home'), text: 'ホーム' }))));
  }
  function again() {
    const r = S.result;
    const body = S.lastStart && S.lastStart.mode === (r && r.mode) ? S.lastStart : { mode: (r && r.mode) || 'study' };
    S.home = S.home ? { ...S.home, active_session: null } : S.home; // 結果画面では完了済み
    startSession(body);
  }

  /* ================= 成績 ================= */
  async function viewStats(ctx, args) {
    const tab = args[0] === 'time' ? 'time' : args[0] === 'b' ? 'b' : 'cat';
    const tpart = ['A', 'B'].includes(args[1]) ? args[1] : 'all';
    const wrap = h('div', { class: 'page' },
      h('div', { class: 'pagehead' }, h('h1', { text: '成績' }), netPill()),
      h('nav', { class: 'subtabs three', 'aria-label': '成績の表示切替' },
        h('a', { href: '#/stats', 'aria-current': tab === 'cat' ? 'page' : null, text: '科目A' }),
        h('a', { href: '#/stats/b', 'aria-current': tab === 'b' ? 'page' : null, text: '科目B' }),
        h('a', { href: '#/stats/time', 'aria-current': tab === 'time' ? 'page' : null, text: '時間分析' })),
      tab === 'time' ? h('div', { class: 'seg', role: 'group', 'aria-label': '時間分析の対象' },
        [['all', '全体'], ['A', '科目A'], ['B', '科目B']].map(([k, n]) => h('button', {
          type: 'button', 'aria-pressed': String(tpart === k), onclick: () => go(`#/stats/time/${k}`), text: n }))) : null);
    const body = h('div', { class: 'page', style: { padding: 0 } }, h('div', { class: 'skeleton', text: '読み込み中…' }));
    wrap.append(body);
    view.replaceChildren(wrap);
    try {
      const frag = tab === 'cat' ? await statsCategories() : tab === 'b' ? await statsB() : await statsTime(tpart);
      if (!ctx.alive()) return;
      body.replaceChildren(...frag);
    } catch (e) {
      if (!ctx.alive()) return;
      body.replaceChildren(h('div', { class: 'errbox' }, h('p', { text: e instanceof NetError ? 'サーバーに接続できません' : e.message }),
        h('button', { class: 'btn primary', type: 'button', onclick: () => route(), text: '再試行' })));
    }
  }

  async function statsCategories() {
    const data = await api('GET', '/api/stats/categories');
    S.cats = data;
    return data.fields.map((f) => {
      const cs = f.categories;
      const total = cs.reduce((a, c) => a + c.total, 0), seen = cs.reduce((a, c) => a + c.seen, 0);
      const wsum = cs.reduce((a, c) => a + (c.accuracy != null ? c.seen : 0), 0);
      const acc = wsum ? cs.reduce((a, c) => a + (c.accuracy != null ? c.accuracy * c.seen : 0), 0) / wsum : null;
      const nT = cs.reduce((a, c) => a + (c.attempts || 0), 0);
      const avg = nT ? cs.reduce((a, c) => a + (c.avg_time_ms || 0) * (c.attempts || 0), 0) / nT : null;
      return h('section', null,
        h('div', { class: 'fieldhead' }, h('h2', { text: f.name }),
          h('span', { class: 'agg', text: `正答率 ${pct(acc)} ・ 学習済 ${seen}/${total} ・ 平均 ${fmtDur(avg)}` })),
        h('ul', { class: 'list' }, cs.map((c) => h('li', null,
          h('button', { class: 'catrow', type: 'button', 'aria-label': `${c.name} の問題を学習する`, onclick: () => startSession({ mode: 'study', category_ids: [c.id] }) },
            h('div', { class: 'top' }, h('span', { class: 'nm', text: c.name }), h('span', { class: 'pc', text: pct(c.accuracy) })),
            hbar(c.accuracy || 0),
            h('div', { class: 'meta' }, h('span', { text: `学習済 ${c.seen}/${c.total}` }), h('span', { text: `平均 ${fmtDur(c.avg_time_ms)}` })))))));
    });
  }

  async function statsB() {
    const d = await api('GET', '/api/stats/b');
    const row = (nm, x, onclick) => {
      const inner = [
        h('div', { class: 'top' }, h('span', { class: 'nm', text: nm }), h('span', { class: 'pc', text: pct(x.accuracy) })),
        hbar(x.accuracy || 0),
        h('div', { class: 'meta' }, h('span', { text: `学習済 ${x.seen}/${x.total}` }), h('span', { text: `平均 ${fmtDur(x.avg_time_ms)}` }))];
      return h('li', null, onclick
        ? h('button', { class: 'catrow', type: 'button', 'aria-label': `${nm} の問題を学習する`, onclick }, inner)
        : h('div', { class: 'catrow static' }, inner));
    };
    const topics = d.topics || [], tags = d.tags || [], diff = d.difficulty || [];
    const out = [];
    out.push(h('section', null, h('div', { class: 'fieldhead' }, h('h2', { text: 'テーマ別' }), h('span', { class: 'agg', text: 'タップで学習' })),
      h('ul', { class: 'list' }, topics.length ? topics.map((x) => row(x.name || B_TOPIC_NAME[x.b_topic] || x.b_topic, x,
        () => startSession({ mode: 'study', part: 'B', b_topics: [x.b_topic] }))) : h('li', { class: 'muted center', text: '科目Bの問題がありません' }))));
    if (tags.length) out.push(h('section', null, h('div', { class: 'fieldhead' }, h('h2', { text: 'タグ別' })),
      h('ul', { class: 'list' }, tags.map((x) => row(x.tag, x)))));
    if (diff.length) out.push(h('section', null, h('div', { class: 'fieldhead' }, h('h2', { text: '難易度別' })),
      h('ul', { class: 'list' }, diff.map((x) => row(x.difficulty || '未設定', x)))));
    return out;
  }

  async function statsTime(part = 'all') {
    const d = await api('GET', `/api/stats/time?part=${part}`);
    const target = (part === 'B' ? (S.settings.target_sec_b || 300) : (S.settings.target_sec || 90)) * 1000;
    const o = d.overall || {};
    const out = [];
    out.push(h('div', { class: 'cards3' },
      h('div', { class: 'stat' }, h('div', { class: 'v', text: fmtDur(o.avg_ms) }), h('div', { class: 'l', text: '平均' })),
      h('div', { class: 'stat' }, h('div', { class: 'v', text: fmtDur(o.median_ms) }), h('div', { class: 'l', text: '中央値' })),
      h('div', { class: 'stat' }, h('div', { class: 'v', text: pct(o.over_target_rate) }), h('div', { class: 'l', text: '目標超過率' }), h('div', { class: 's', text: `目標 ${fmtDur(target)}` }))));

    const cats = (d.by_category || []).filter((c) => c.n > 0).sort((a, b) => b.avg_ms - a.avg_ms);
    if (cats.length) {
      const max = Math.max(target, ...cats.map((c) => c.avg_ms)) * 1.05;
      out.push(h('section', null, h('div', { class: 'section-title', text: '分野別の平均時間（点線＝目標）' }),
        h('div', { class: 'card' }, cats.map((c) => h('div', { class: 'brow' },
          h('div', { class: 'line' }, h('span', { text: c.name }), h('span', { text: `${fmtDur(c.avg_ms)}（${c.n}問）` })),
          hbar(c.avg_ms / max, target / max))))));
    }

    const cv = d.calc_vs_noncalc;
    if (cv && part !== 'B') {
      const card = (name, x) => h('div', { class: 'stat' },
        h('div', { class: 'l', text: name }),
        h('div', { class: 'v', text: x && x.n ? fmtDur(x.avg_ms) : '—' }),
        h('div', { class: 's', text: x && x.n ? `正答率 ${pct(x.accuracy)}・${x.n}問` : 'データなし' }));
      out.push(h('section', null, h('div', { class: 'section-title', text: '計算問題 vs 非計算問題' }),
        h('div', { class: 'cards2' }, card('計算問題', cv.calc), card('非計算問題', cv.noncalc))));
    }

    const sa = d.speed_accuracy || [];
    if (sa.length) {
      out.push(h('section', null, h('div', { class: 'section-title', text: '解答時間帯ごとの正答率' }),
        h('div', { class: 'card' }, sa.map((b) => h('div', { class: 'brow' },
          h('div', { class: 'line' }, h('span', { text: b.bucket }), h('span', { text: b.n ? `${pct(b.accuracy)}（${b.n}問）` : 'データなし' })),
          hbar(b.n ? b.accuracy : 0))))));
    }

    out.push(h('section', null, h('div', { class: 'section-title', text: '直近30日' }),
      h('div', { class: 'card' }, dailyChart(d.daily || []),
        h('div', { class: 'legend' }, h('span', null, h('i'), '回答数'), h('span', null, h('i', { class: 'ln' }), '正答率')))));

    const sl = d.slowest || [];
    if (sl.length) {
      out.push(h('section', null, h('div', { class: 'section-title', text: '時間がかかった問題（タップで詳細）' }),
        h('ul', { class: 'list' }, sl.map((x) => h('li', null,
          h('button', { class: `hrow ${x.is_correct ? 'ok' : 'ng'}`, type: 'button', onclick: () => go(`#/q/${encodeURIComponent(x.question_id)}`) },
            h('span', { class: 'mk', 'aria-label': x.is_correct ? '正解' : '不正解' }, icon(x.is_correct ? 'check' : 'x')),
            h('span', { class: 'grow' }, h('span', { text: x.label }), h('br'), h('span', { class: 'small muted', text: x.category_name })),
            h('span', { class: 't' }, h('b', { text: fmtDur(x.time_ms) }))))))));
    }
    return out;
  }

  function dailyChart(days) {
    const W = 360, H = 190, L = 28, R = 30, T = 10, B = 26;
    const pw = W - L - R, ph = H - T - B, N = 30, step = pw / N;
    const keys = [];
    const now = new Date();
    for (let i = N - 1; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      keys.push(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
    }
    const map = new Map(days.map((x) => [x.date, x]));
    const maxN = Math.max(4, ...days.map((x) => x.n));
    const niceMax = maxN <= 10 ? Math.ceil(maxN / 2) * 2 : Math.ceil(maxN / 5) * 5;
    const total = days.reduce((a, x) => a + x.n, 0);
    const svg = sv('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `直近30日の回答数と正答率。合計${total}問。` });
    const y = (v) => T + ph * (1 - v);
    [0, 0.5, 1].forEach((g) => {
      svg.append(sv('line', { class: 'grid', x1: L, x2: W - R, y1: y(g), y2: y(g) }));
      svg.append(sv('text', { x: W - R + 4, y: y(g) + 3.5, text: null }, `${g * 100}%`));
      svg.append(sv('text', { x: L - 4, y: y(g) + 3.5, 'text-anchor': 'end' }, String(Math.round(niceMax * g))));
    });
    let seg = [];
    const segs = [];
    keys.forEach((k, i) => {
      const x = L + i * step, d = map.get(k);
      if (d && d.n > 0) {
        const bh = (d.n / niceMax) * ph;
        svg.append(sv('rect', { class: 'cbar', x: x + step * 0.18, y: T + ph - bh, width: step * 0.64, height: bh }));
        if (d.accuracy != null) seg.push([x + step / 2, y(d.accuracy)]);
        else if (seg.length) { segs.push(seg); seg = []; }
      } else if (seg.length) { segs.push(seg); seg = []; }
      if (i % 7 === 0) {
        const dt = new Date(k + 'T00:00:00');
        svg.append(sv('text', { x: x + step / 2, y: H - 8, 'text-anchor': 'middle' }, `${dt.getMonth() + 1}/${dt.getDate()}`));
      }
    });
    if (seg.length) segs.push(seg);
    segs.forEach((s) => {
      if (s.length > 1) svg.append(sv('path', { class: 'cline', d: s.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ') }));
      s.forEach((p) => svg.append(sv('circle', { class: 'cdot', cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: 2.6 })));
    });
    return svg;
  }

  /* ================= 履歴 ================= */
  const hist = { items: [], done: false };
  async function viewHistory(ctx) {
    hist.items = []; hist.done = false;
    const list = h('ul', { class: 'list' });
    const more = h('button', { class: 'btn block', type: 'button', text: 'さらに表示' });
    const empty = h('p', { class: 'muted center', text: '履歴はまだありません' });
    view.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'pagehead' }, h('h1', { text: '履歴' }), netPill()), list, more, empty));
    more.hidden = true; empty.hidden = true;
    async function load() {
      more.disabled = true;
      try {
        const rows = await api('GET', `/api/history?limit=50&offset=${hist.items.length}`);
        if (!ctx.alive()) return;
        hist.items.push(...rows); if (rows.length < 50) hist.done = true;
        rows.forEach((x) => list.append(h('li', null, h('button', {
          class: `hrow ${x.is_correct == null ? '' : x.is_correct ? 'ok' : 'ng'}`, type: 'button', onclick: () => go(`#/q/${encodeURIComponent(x.question_id)}`),
        },
          h('span', { class: 'mk', 'aria-label': x.chosen == null ? '未回答' : x.is_correct ? '正解' : '不正解' }, x.chosen == null ? icon('dash') : icon(x.is_correct ? 'check' : 'x')),
          h('span', { class: 'grow' }, h('span', { text: x.label }), h('br'),
            h('span', { class: 'small muted', text: `${x.part === 'B' ? '科目B・' : ''}${x.category_name}・${MODE_NAME[x.mode] || ''}${x.chosen ? `・${x.chosen}${x.answer && x.answer !== x.chosen ? `→正解${x.answer}` : ''}` : ''}` })),
          h('span', { class: 't' }, fmtDur(x.time_ms), h('br'), fmtDateTime(x.answered_at))))));
        empty.hidden = hist.items.length > 0;
        more.hidden = hist.done; more.disabled = false;
      } catch (e) {
        more.disabled = false; more.hidden = false;
        toast(e instanceof NetError ? 'サーバーに接続できません' : e.message);
      }
    }
    more.addEventListener('click', load);
    await load();
  }

  /* ================= 単問詳細 ================= */
  async function viewQuestion(ctx, args) {
    renderLoading();
    const d = await api('GET', `/api/questions/${encodeURIComponent(args[0] || '')}`);
    if (!ctx.alive()) return;
    const attempts = d.attempts || [];
    const lastAtt = attempts[0] || d.attempt || null;
    const answer = d.answer ?? (lastAtt && lastAtt.answer) ?? null;
    const lastChosen = lastAtt ? lastAtt.chosen : null;
    const allEmpty = (d.choices || []).every((c) => !c.text);
    const choices = h('div', { class: `choices${allEmpty ? ' grid' : ''}${d.part === 'B' ? ' two' : ''}`, style: { padding: '8px 0' } }, (d.choices || []).map((c) => {
      const isAns = answer && c.label === answer;
      const isMine = lastChosen && c.label === lastChosen;
      const cls = isAns ? 'correct' : isMine ? 'wrong' : answer ? 'dim' : '';
      return h('div', { class: `choice locked ${cls}` }, h('span', { class: 'clabel', text: c.label }), c.text ? h('span', { class: 'ctext', text: c.text }) : null,
        h('span', { class: 'cmark' }, isAns ? [icon('check'), '正解'] : isMine ? [icon('x'), '直近の回答'] : null));
    }));
    const attRows = attempts.map((a) => h('li', { class: 'srow', style: { minHeight: '40px' } },
      h('span', { class: 'lbl' }, h('span', { text: fmtDateTime(a.answered_at) }), a.mode ? h('small', { text: MODE_NAME[a.mode] || '' }) : null),
      h('span', null, a.chosen == null ? '未回答' : [`${a.chosen} `, a.is_correct ? h('span', { class: 'pill', style: { background: 'var(--ok-bg)', color: 'var(--ok-text)' } }, icon('check'), '正解') : h('span', { class: 'pill', style: { background: 'var(--ng-bg)', color: 'var(--ng-text)' } }, icon('x'), '不正解'), ` ${fmtDur(a.time_ms)}`])));
    view.replaceChildren(h('div', { class: 'page' },
      h('div', { class: 'pagehead' },
        h('button', { class: 'btn small', type: 'button', onclick: () => (history.length > 1 ? history.back() : go('#/history')) }, icon('back'), '戻る'),
        h('span', { class: 'muted small', text: d.category_name || '' })),
      h('h2', { text: `${d.part === 'B' ? '科目B ' : ''}${d.exam_label || ''} 問${d.number ?? ''}${d.sub_label ? ` ${d.sub_label}` : ''}${d.is_calc ? '（計算）' : ''}` }),
      d.body && !(d.part === 'B' && (d.image_urls || []).length) ? h('p', { class: 'qbody', text: d.body }) : null,
      d.part === 'B' ? bImages(d, false)
        : (d.image_url ? h('button', { class: 'figure', type: 'button', 'aria-label': '図を拡大して表示', onclick: () => openViewer(d.image_url) }, h('img', { src: d.image_url, alt: '問題の図・表' })) : null),
      choices,
      explBtn(d.id, d.explanation_image_urls, d.explanation_source, false, d),
      d.explanation_url ? h('a', { class: 'btn link', href: d.explanation_url, target: '_blank', rel: 'noopener', text: '過去問道場で解説を見る ↗' }) : null,
      h('p', { class: 'qsource', text: d.source_label || '' }),
      attRows.length ? h('section', null, h('div', { class: 'section-title', text: '回答履歴' }), h('ul', { class: 'card list', style: { gap: 0 } }, attRows)) : null));
  }

  /* ================= 設定 ================= */
  let pendingPatch = {}, saveTimer = null;
  function setSetting(k, v) {
    S.settings[k] = v; pendingPatch[k] = v;
    applySettings(S.settings);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSettings, 300);
  }
  async function flushSettings() {
    const p = pendingPatch; pendingPatch = {};
    if (!Object.keys(p).length) return;
    try {
      await api('PUT', '/api/settings', p);
      if (S.home) { S.home.settings = { ...S.home.settings, ...p }; }
      toast('保存しました', 1400);
    } catch (e) {
      pendingPatch = { ...p, ...pendingPatch };
      toast(e instanceof NetError ? '保存できませんでした（オンライン復帰後に再送）' : `保存できませんでした：${e.message}`, 2800);
    }
  }

  async function viewSettings(ctx) {
    renderLoading();
    let cats;
    try { cats = (await api('GET', '/api/stats/categories')).fields; S.cats = { fields: cats }; }
    catch (e) { cats = S.cats ? S.cats.fields : null; if (!cats) throw e; }
    if (!ctx.alive()) return;
    const st = S.settings;
    const allIds = cats.flatMap((f) => f.categories.map((c) => c.id));
    const sel = () => new Set(st.category_ids && st.category_ids.length ? st.category_ids : allIds);

    const root = h('div', { class: 'page' });
    const dataEl = STANDALONE ? dataCard() : null;
    const draw = () => {
      const selected = sel();
      const totalQ = cats.reduce((a, f) => a + f.categories.filter((c) => selected.has(c.id)).reduce((b, c) => b + c.total, 0), 0);
      const seg = (key, opts, fmt) => h('div', { class: 'seg', role: 'group' }, opts.map((o) => h('button', {
        type: 'button', 'aria-pressed': String(Number(st[key]) === o), onclick: () => { setSetting(key, o); draw(); }, text: fmt(o),
      })));
      const setCats = (ids) => {
        const set = new Set(ids);
        if (!set.size) { toast('分野は1つ以上選んでください'); return draw(); }
        setSetting('category_ids', set.size === allIds.length ? [] : [...set].sort((a, b) => a - b));
        draw();
      };
      const fieldBlocks = cats.map((f) => {
        const ids = f.categories.map((c) => c.id);
        const nSel = ids.filter((i) => selected.has(i)).length;
        const fTotal = f.categories.reduce((a, c) => a + c.total, 0);
        const fc = h('input', { type: 'checkbox', 'aria-label': `${f.name}を一括選択` });
        fc.checked = nSel === ids.length; fc.indeterminate = nSel > 0 && nSel < ids.length;
        fc.addEventListener('change', () => { const s = sel(); ids.forEach((i) => (fc.checked ? s.add(i) : s.delete(i))); setCats([...s]); });
        return h('div', { class: 'setgroup', style: { gap: '2px' } },
          h('label', { class: 'chk fieldchk' }, fc, h('span', { class: 'grow', text: f.name }), h('span', { class: 'badge', text: `${fTotal}問` })),
          f.categories.map((c) => {
            const cb = h('input', { type: 'checkbox' });
            cb.checked = selected.has(c.id);
            cb.addEventListener('change', () => { const s = sel(); if (cb.checked) s.add(c.id); else s.delete(c.id); setCats([...s]); });
            return h('label', { class: 'chk sub' }, cb, h('span', { class: 'grow', text: c.name }), h('span', { class: 'badge', text: String(c.total) }));
          }));
      });
      const bTopics = Array.isArray(st.b_topics) && st.b_topics.length ? st.b_topics : ['pseudo', 'security'];
      const tB = Math.min(8, Math.max(3, Math.round((st.target_sec_b || 300) / 60)));
      const fontOpts = [0.9, 1.0, 1.15, 1.3], fontName = { 0.9: '小', 1: '標準', 1.15: '大', 1.3: '特大' };
      const themeName = { auto: '自動', light: 'ライト', dark: 'ダーク' };
      root.replaceChildren(
        h('div', { class: 'pagehead' }, h('h1', { text: '設定' }), netPill()),
        h('section', { class: 'card' },
          h('div', { class: 'srow' },
            h('div', { class: 'lbl' }, '計算問題を含める', h('small', { text: 'OFFにすると計算が必要な問題を出題しません（模試は除く）' })),
            h('button', { class: 'switch', type: 'button', role: 'switch', 'aria-checked': String(!!st.include_calc), 'aria-label': '計算問題を含める',
              onclick: () => { setSetting('include_calc', !st.include_calc); draw(); } }))),
        h('section', { class: 'card setgroup' },
          h('div', { class: 'lbl' }, '1回の問題数', h('small', { class: 'muted', style: { display: 'block' }, text: `${st.session_size}問 ≒ 約${st.session_size}分` })),
          seg('session_size', [5, 8, 10, 15], (o) => `${o}問`)),
        h('section', { class: 'card setgroup', 'aria-label': '科目B の設定' },
          h('h2', { text: '科目B' }),
          h('div', { class: 'lbl' }, '1回の問題数', h('small', { class: 'muted', style: { display: 'block' }, text: `${st.session_size_b}問 ≒ 約${st.session_size_b * 5}分` })),
          seg('session_size_b', [1, 2, 3, 4], (o) => `${o}問`),
          h('div', { class: 'lbl', style: { marginTop: '6px' } }, 'テーマ', h('small', { class: 'muted', style: { display: 'block' }, text: bTopics.length === 1 ? `${B_TOPIC_NAME[bTopics[0]]}のみ出題` : '両方から出題' })),
          Object.entries(B_TOPIC_NAME).map(([k, n]) => {
            const cb = h('input', { type: 'checkbox' });
            cb.checked = bTopics.includes(k);
            cb.addEventListener('change', () => {
              const cur = new Set(bTopics);
              if (cb.checked) cur.add(k); else cur.delete(k);
              if (!cur.size) { toast('テーマは1つ以上選んでください'); return draw(); }
              setSetting('b_topics', cur.size === 2 ? [] : [...cur]); draw();
            });
            return h('label', { class: 'chk' }, cb, h('span', { class: 'grow', text: n }));
          }),
          h('div', { class: 'srow' },
            h('div', { class: 'lbl' }, '目標時間（1問）', h('small', { text: '本試験は100分で20問＝5分。超えると時計の色が変わります' })),
            h('div', { class: 'stepper' },
              h('button', { class: 'btn', type: 'button', 'aria-label': '1分短くする', disabled: tB <= 3, onclick: () => { setSetting('target_sec_b', (tB - 1) * 60); draw(); }, text: '−' }),
              h('b', { 'aria-live': 'polite', text: `${tB}分` }),
              h('button', { class: 'btn', type: 'button', 'aria-label': '1分長くする', disabled: tB >= 8, onclick: () => { setSetting('target_sec_b', (tB + 1) * 60); draw(); }, text: '＋' })))),
        h('section', { class: 'card setgroup' },
          h('div', { class: 'lbl' }, '出題する分野', h('small', { class: 'muted', style: { display: 'block' }, text: st.category_ids && st.category_ids.length ? `${selected.size}分野 ・ ${totalQ}問から出題` : `全分野 ・ ${totalQ}問から出題` })),
          fieldBlocks),
        h('section', { class: 'card setgroup' },
          h('div', { class: 'lbl', text: '文字サイズ' }),
          h('div', { class: 'seg', role: 'group' }, fontOpts.map((o) => h('button', {
            type: 'button', 'aria-pressed': String(Number(st.font_scale) === o), onclick: () => { setSetting('font_scale', o); draw(); }, text: fontName[o] }))),
          h('p', { class: 'preview', text: '基本情報技術者試験 科目A：この大きさで問題文が表示されます。1234 ABCD' })),
        h('section', { class: 'card setgroup' },
          h('div', { class: 'lbl', text: 'テーマ' }),
          h('div', { class: 'seg', role: 'group' }, ['auto', 'light', 'dark'].map((o) => h('button', {
            type: 'button', 'aria-pressed': String(st.theme === o), onclick: () => { setSetting('theme', o); draw(); }, text: themeName[o] })))),
        h('section', { class: 'card' },
          h('div', { class: 'srow' },
            h('div', { class: 'lbl' }, '目標時間（科目A・1問）', h('small', { text: '本試験は1問あたり90秒。超えると時計の色が変わります' })),
            h('div', { class: 'stepper' },
              h('button', { class: 'btn', type: 'button', 'aria-label': '15秒短くする', disabled: st.target_sec <= 30, onclick: () => { setSetting('target_sec', Math.max(30, st.target_sec - 15)); draw(); }, text: '−' }),
              h('b', { 'aria-live': 'polite', text: `${st.target_sec}秒` }),
              h('button', { class: 'btn', type: 'button', 'aria-label': '15秒長くする', disabled: st.target_sec >= 300, onclick: () => { setSetting('target_sec', Math.min(300, st.target_sec + 15)); draw(); }, text: '＋' })))),
        dataEl);
    };
    draw();
    view.replaceChildren(root);
    S.cleanup = () => { if (Object.keys(pendingPatch).length) { clearTimeout(saveTimer); flushSettings(); } };
  }

  /* ================= スタンドアロン: データ管理・オンボーディング ================= */
  const fmtBytes = (n) => (n == null ? '—' : n >= 1048576 ? `${(n / 1048576).toFixed(n >= 1.07e8 ? 0 : 1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  const fmtDay = (iso) => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}` : '—'; };
  const progressBar = () => {
    const fill = h('div', { class: 'pfill' }), label = h('p', { class: 'muted small center', role: 'status', text: '' });
    const bar = h('div', { class: 'pbar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, fill);
    const el = h('div', { class: 'pwrap', hidden: true }, bar, label);
    return { el, set(r, t) { el.hidden = false; const v = Math.round(r * 100); fill.style.width = `${v}%`; bar.setAttribute('aria-valuenow', String(v)); label.textContent = `${t || ''} ${v}%`; } };
  };
  function pickZip(onFile) {
    const inp = h('input', { type: 'file', accept: '.zip,application/zip', hidden: true });
    inp.addEventListener('change', () => { const f = inp.files && inp.files[0]; if (f) onFile(f); inp.value = ''; });
    return inp;
  }
  const afterDataChange = () => { S.home = null; S.homeStale = true; S.cats = null; };

  function showOnboarding() {
    return new Promise(async (resolve) => {
      document.body.dataset.route = 'onboard';
      const info = await FEPack.publicInfo().catch(() => ({ size: null }));
      const pb = progressBar();
      const err = h('p', { class: 'small', style: { color: 'var(--ng-text)' }, hidden: true });
      let busy = false;
      const run = async (fn, btns) => {
        if (busy) return; busy = true; err.hidden = true; btns.forEach((b) => { b.disabled = true; });
        try { await fn(); resolve(); } catch (e) {
          err.hidden = false; err.textContent = `失敗しました：${(e && e.message) || e}`; pb.el.hidden = true;
          btns.forEach((b) => { b.disabled = false; }); busy = false;
        }
      };
      const dl = h('button', { class: 'btn primary hero', type: 'button' },
        h('span', { text: `問題データをダウンロード` }),
        h('span', { class: 'hero-sub', text: `${info.size ? `約 ${info.size >= 1048576 ? Math.round(info.size / 1048576) + ' MB' : fmtBytes(info.size)}・` : ''}Wi-Fi 推奨` }));
      const file = pickZip((f) => run(() => FEPack.installPack(f, pb.set), [dl, pick]));
      const pick = h('button', { class: 'btn ghost small', type: 'button', onclick: () => file.click(), text: '書籍付録パックを読み込む（任意）' });
      dl.addEventListener('click', () => run(() => FEPack.downloadPublicPack(null, pb.set), [dl, pick]));
      view.replaceChildren(h('div', { class: 'page onboard' },
        h('div', { class: 'pagehead' }, h('h1', { text: 'FE Study' })),
        h('p', { text: '基本情報技術者試験の学習アプリです。最初に問題データを端末へ保存すると、以後は電波がなくても使えます。' }),
        dl, pb.el, err,
        h('div', { class: 'center' }, pick, file),
        h('section', { class: 'card small' },
          h('b', { text: 'ホーム画面に追加すると安心です' }),
          h('p', { class: 'muted', text: 'iPhone は Safari の共有メニュー →「ホーム画面に追加」。追加しないと、7日間アプリを開かなかったときに保存データが消える場合があります。学習記録は設定からバックアップできます。' }))));
    });
  }

  function dataCard() {
    const root = h('section', { class: 'card setgroup', 'aria-label': 'データ管理' });
    const pb = progressBar();
    let busy = false;
    const run = async (fn, okMsg) => {
      if (busy) return; busy = true;
      try { const r = await fn(); afterDataChange(); toast(okMsg(r), 2800); } catch (e) { toast(`失敗しました：${(e && e.message) || e}`, 3500); }
      busy = false; pb.el.hidden = true; draw();
    };
    const line = (k, v) => h('div', { class: 'srow' }, h('div', { class: 'lbl', text: k }), h('span', { class: 'muted small', text: v }));
    const privFile = pickZip((f) => run(() => FEPack.installPack(f, pb.set), (r) => `${r.manifest.kind === 'private' ? '書籍付録' : '公開'}パックを読み込みました（${r.questions}問）`));
    const bakIn = h('input', { type: 'file', accept: '.json,application/json', hidden: true });
    bakIn.addEventListener('change', () => {
      const f = bakIn.files && bakIn.files[0]; bakIn.value = '';
      if (f) run(() => FEBackup.importData(f, 'merge'), (r) => `学習記録を読み込みました（回答 ${r.attempts_added}件追加）`);
    });
    async function wipe() {
      if (!(await confirmSheet('すべて削除しますか？', '問題データと学習記録（回答履歴・設定）がこの端末から消えます。', '次へ', 'やめる'))) return;
      if (!(await confirmSheet('本当に削除しますか？', 'この操作は元に戻せません。先にバックアップの書き出しをおすすめします。', 'すべて削除する', 'やめる'))) return;
      try {
        await FEPack.clearAll();
        ['fe.ui', 'fe.queue', 'fe.result', 'fe.lastStart'].forEach((k) => { try { localStorage.removeItem(k); sessionStorage.removeItem(k); } catch { /* noop */ } });
        history.replaceState(null, '', '#/home'); location.reload();
      } catch (e) { toast(`削除できませんでした：${e.message}`, 3500); }
    }
    async function draw() {
      let st = null, est = null, persisted = null;
      try { st = await FEPack.packStatus(); } catch { /* noop */ }
      try { est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null; } catch { /* noop */ }
      try { persisted = navigator.storage && navigator.storage.persisted ? await navigator.storage.persisted() : null; } catch { /* noop */ }
      const pk = (k) => (st && st[k] ? `${st[k].manifest.question_count}問・${fmtDay(st[k].manifest.created_at || st[k].installed_at)}` : '未導入');
      root.replaceChildren(
        h('h2', { text: 'データ管理' }),
        line('公開パック', pk('public')), line('書籍付録パック', pk('private')),
        line('保存済みの問題', st ? `${st.question_count}問` : '—'),
        line('使用量', est ? `${fmtBytes(est.usage)}${est.quota ? ` / ${fmtBytes(est.quota)}` : ''}` : '—'),
        line('永続化', persisted == null ? '—' : persisted ? '有効（消えにくい）' : '未許可（ホーム画面に追加すると許可されやすい）'),
        pb.el,
        h('button', { class: 'btn block', type: 'button', onclick: () => run(() => FEPack.downloadPublicPack(null, pb.set), (r) => `公開パックを更新しました（${r.questions}問）`), text: '公開パックを更新' }),
        h('button', { class: 'btn block', type: 'button', onclick: () => privFile.click(), text: '書籍付録パックを読み込む' }), privFile,
        h('button', { class: 'btn block', type: 'button', onclick: () => run(() => FEBackup.download(), (r) => `書き出しました（回答 ${r.attempts}件）`), text: '学習記録を書き出す' }),
        h('button', { class: 'btn block', type: 'button', onclick: () => bakIn.click(), text: '学習記録を読み込む' }), bakIn,
        h('button', { class: 'btn block danger', type: 'button', onclick: wipe, text: 'すべて削除' }));
    }
    draw();
    return root;
  }

  /* ================= 起動 ================= */
  async function boot() {
    const ui = LS.get('fe.ui', null);
    if (ui) applySettings({ ...DEFAULTS, ...ui });
    if (!location.hash) history.replaceState(null, '', '#/home');
    window.addEventListener('hashchange', route);
    $('#sheet').addEventListener('click', (e) => { if (e.target === $('#sheet')) closeSheet(); });
    updateNet();
    if (STANDALONE) {
      try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch { /* noop */ }
      let st = null;
      try { st = await FEPack.packStatus(); } catch { /* local/*.js 未読込など */ }
      if (st && st.question_count === 0) { await showOnboarding(); afterDataChange(); history.replaceState(null, '', '#/home'); }
    }
    // 起動時に /api/home を 1 回だけ呼ぶ（以降のホーム表示は stale 時のみ再取得）
    try {
      const home = await api('GET', '/api/home');
      S.home = home; S.settings = { ...DEFAULTS, ...home.settings }; applySettings(S.settings);
      flushQueue();
    } catch { /* ホーム側でエラー表示 */ }
    route();
  }
  boot();

  // Service Worker（HTTPS または localhost のときのみ有効。tailscale serve は HTTPS）
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => { navigator.serviceWorker.register('sw.js', { scope: './' }).catch(() => {}); });
  }
})();
