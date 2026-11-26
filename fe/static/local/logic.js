/* FE Study — 出題・集計の純粋関数（SPEC 7.4）。Python 版 app/selection.py, stats.py, api.py の移植。
 * UMD: ブラウザは window.FELogic、Node は module.exports。DOM / IndexedDB には触れない。
 *
 * 入力の形（SPEC 7.2 / 7.3 と同じ）:
 *   question: {id, exam_code, exam_label, number, category_id, body, choices[], answer, image_path,
 *              is_calc(bool), pool, part, images[], group_id, sub_label, b_topic, tags[], difficulty,
 *              explanation_images[], explanation_source, source_label, explanation_url, ...}
 *   attempt : {id, session_id, question_id, chosen|null, is_correct(0|1), time_ms, answered_at}
 *   session : {id, mode, status, question_ids[], current_index, current_elapsed_ms, time_limit_sec,
 *              options{}, started_at, updated_at, ended_at}
 *   rng     : () => [0,1) の乱数（Math.random 互換）
 *   日時    : ローカル時刻 'YYYY-MM-DD HH:MM:SS'、today は 'YYYY-MM-DD'
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FELogic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ================= 定数 ================= */
  const FIELD_NAMES = { te: 'テクノロジ系', ma: 'マネジメント系', st: 'ストラテジ系' };
  const MAJORS = { 1: '基礎理論', 2: 'コンピュータシステム', 3: '技術要素', 4: '開発技術', 5: 'プロジェクトマネジメント',
    6: 'サービスマネジメント', 7: 'システム戦略', 8: '経営戦略', 9: '企業と法務' };
  // [中分類番号, 大分類番号, 中分類名]（app/categories.py と同一）
  const MIDDLES = [
    [1, 1, '基礎理論'], [2, 1, 'アルゴリズムとプログラミング'], [3, 2, 'コンピュータ構成要素'],
    [4, 2, 'システム構成要素'], [5, 2, 'ソフトウェア'], [6, 2, 'ハードウェア'],
    [7, 3, 'ユーザーインタフェース'], [8, 3, '情報メディア'], [9, 3, 'データベース'],
    [10, 3, 'ネットワーク'], [11, 3, 'セキュリティ'], [12, 4, 'システム開発技術'],
    [13, 4, 'ソフトウェア開発管理技術'], [14, 5, 'プロジェクトマネジメント'], [15, 6, 'サービスマネジメント'],
    [16, 6, 'システム監査'], [17, 7, 'システム戦略'], [18, 7, 'システム企画'], [19, 8, '経営戦略マネジメント'],
    [20, 8, '技術戦略マネジメント'], [21, 8, 'ビジネスインダストリ'], [22, 9, '企業活動'], [23, 9, '法務'],
  ];
  const fieldOf = (id) => (id <= 13 ? 'te' : id <= 16 ? 'ma' : 'st');
  const CATEGORIES = MIDDLES.map(([id, major, name]) => ({
    id, field: fieldOf(id), major_no: major, major_name: MAJORS[major], name,
  }));
  // 実行時の分類表。既定は上の 23 中分類。パックの categories（meta 'categories'）があれば setCategories で差し替える
  let CATS = CATEGORIES;
  let CATEGORY_BY_ID = new Map(CATS.map((c) => [c.id, c]));
  function setCategories(rows) {
    const ok = Array.isArray(rows) && rows.length && rows.every((c) => c && isInt0(c.id) && c.field && c.name);
    CATS = ok ? rows.slice().sort((a, b) => a.id - b.id) : CATEGORIES;
    CATEGORY_BY_ID = new Map(CATS.map((c) => [c.id, c]));
  }
  function isInt0(v) { return typeof v === 'number' && Number.isInteger(v); }

  const DEFAULT_SETTINGS = {
    include_calc: true, session_size: 8, category_ids: [], font_scale: 1.0, theme: 'auto', target_sec: 90,
    session_size_b: 2, target_sec_b: 300, b_topics: [],
  };
  const LABELS = ['ア', 'イ', 'ウ', 'エ'];
  const LABELS_B = Array.from('アイウエオカキクケコ');
  const B_TOPICS = ['pseudo', 'security'];
  const B_TOPIC_NAMES = { pseudo: '擬似言語', security: '情報セキュリティ' };
  const DIFFICULTIES = ['やさしい', 'ふつう', '難しい'];
  const B_MOCK_MAX = 12;

  /* ================= エラー ================= */
  class ApiError extends Error {
    constructor(error, status = 400) {
      super(error);
      this.name = 'ApiError';
      this.status = status;
      this.error = error;
    }
  }

  /* ================= 小物（Python との丸め・順序の一致） ================= */
  const isInt = (v) => typeof v === 'number' && Number.isInteger(v);
  const isStr = (v) => typeof v === 'string';
  const partOf = (q) => q.part || 'A';

  /** Python の round(x)（銀行丸め）。x >= 0 を想定 */
  function pyRound(x) {
    const f = Math.floor(x);
    const d = x - f;
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }
  /** Python の round(x, 4)。厳密な二進小数の同点（x = 奇数/32）は偶数丸め */
  function round4(x) {
    const t = x * 32;
    if (Number.isInteger(t) && Math.abs(t % 2) === 1) {
      const n = x * 1e4, f = Math.floor(n);
      return (f % 2 === 0 ? f : f + 1) / 1e4;
    }
    return Number(x.toFixed(4));
  }
  const avgInt = (v) => (v.length ? Math.trunc(v.reduce((a, b) => a + b, 0) / v.length) : null);
  /** statistics.median（偶数個は中央 2 値の平均） */
  function median(v) {
    const s = v.slice().sort((a, b) => a - b), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }

  function shuffle(arr, rng) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(rng() * (i + 1));
      const t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  const nat = (s) => String(s || '').split(/(\d+)/).map((t, i) => (i % 2 === 1 ? parseInt(t, 10) : t));
  function cmpScalar(x, y) { return x < y ? -1 : x > y ? 1 : 0; }
  function cmpList(a, b) {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) {
      const c = cmpScalar(a[i], b[i]);
      if (c) return c;
    }
    return a.length - b.length;
  }

  function dayOf(ts) { return String(ts || '').slice(0, 10); }
  function addDays(day, n) {
    const [y, m, d] = day.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d) + n * 86400000).toISOString().slice(0, 10);
  }
  const todayOf = (now) => String(now).slice(0, 10);

  /** 問題ごとの最新の回答（chosen が null でないもの、attempt.id 最大） */
  function latestMap(attempts) {
    const m = new Map();
    for (const a of attempts) {
      if (a.chosen == null) continue;
      const c = m.get(a.question_id);
      if (!c || a.id > c.id) m.set(a.question_id, a);
    }
    return m;
  }
  const valid = (attempts) => attempts.filter((a) => a.chosen != null);
  const okInt = (a) => (a.is_correct ? 1 : 0);
  const questionMap = (questions) => new Map(questions.map((q) => [q.id, q]));
  const labelOf = (q) => `${q.exam_label} 問${q.number}` + (q.sub_label ? ` ${q.sub_label}` : '');
  const catName = (id) => (CATEGORY_BY_ID.get(id) || {}).name;

  /* ================= 検証 ================= */
  function validateCategoryIds(v) {
    if (!Array.isArray(v) || !v.every((x) => isInt(x) && x >= 1 && x <= 23)) {
      throw new ApiError('category_ids は 1〜23 の整数配列で指定してください');
    }
    return Array.from(new Set(v)).sort((a, b) => a - b);
  }
  function validateBTopics(v) {
    if (!Array.isArray(v) || !v.every((x) => isStr(x) && B_TOPICS.includes(x))) {
      throw new ApiError('b_topics は pseudo/security の配列で指定してください');
    }
    return B_TOPICS.filter((t) => v.includes(t));
  }
  function validateSettings(d) {
    const out = {};
    for (const k of Object.keys(d)) {
      let v = d[k];
      switch (k) {
        case 'include_calc':
          if (typeof v !== 'boolean') throw new ApiError('include_calc は真偽値です');
          break;
        case 'session_size':
          if (!isInt(v) || v < 5 || v > 15) throw new ApiError('session_size は 5〜15 です');
          break;
        case 'category_ids': v = validateCategoryIds(v); break;
        case 'font_scale':
          if (typeof v !== 'number' || ![0.9, 1.0, 1.15, 1.3].includes(v)) throw new ApiError('font_scale は 0.9/1.0/1.15/1.3 です');
          break;
        case 'theme':
          if (!['auto', 'light', 'dark'].includes(v)) throw new ApiError('theme は auto/light/dark です');
          break;
        case 'target_sec':
          if (!isInt(v) || v < 10 || v > 600) throw new ApiError('target_sec は 10〜600 です');
          break;
        case 'session_size_b':
          if (!isInt(v) || v < 1 || v > 4) throw new ApiError('session_size_b は 1〜4 です');
          break;
        case 'target_sec_b':
          if (!isInt(v) || v < 30 || v > 1800) throw new ApiError('target_sec_b は 30〜1800 です');
          break;
        case 'b_topics': v = validateBTopics(v); break;
        default: throw new ApiError(`未知の設定キー: ${k}`);
      }
      out[k] = v;
    }
    return out;
  }
  /** 保存済み設定（{key: value}）に既定値を補って返す */
  function mergeSettings(stored) {
    const s = JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
    for (const k of Object.keys(s)) if (stored && stored[k] !== undefined) s[k] = stored[k];
    return s;
  }

  /* ================= 出題（selection.py） ================= */
  function takeWeighted(items, weight, n, rng) {
    const keyed = items.map((it) => ({ it, k: Math.pow(rng(), 1.0 / weight(it)) }));
    keyed.sort((a, b) => b.k - a.k); // 安定ソート。Python の reverse=True と同じく同値は元の順
    return keyed.slice(0, n).map((x) => x.it);
  }
  const quotas = (size) => {
    const q = { wrong: pyRound(size * 0.3), unseen: pyRound(size * 0.5), slow: pyRound(size * 0.1) };
    q.mastered = Math.max(0, size - (q.wrong + q.unseen + q.slow));
    return q;
  };
  /** 枠配分で選ぶ（A・B 共通の骨格）。pools は {wrong, unseen, slow, mastered} の配列、key は重複判定用 */
  function fillByQuota(pools, size, n, key) {
    const quota = quotas(size);
    const chosen = [], used = new Set();
    const order = ['wrong', 'unseen', 'slow', 'mastered'];
    for (const name of order) {
      for (const r of pools[name].slice(0, quota[name])) { chosen.push(r); used.add(key(r)); }
    }
    for (const name of order) {
      for (const r of pools[name]) {
        if (chosen.length >= n) break;
        if (!used.has(key(r))) { chosen.push(r); used.add(key(r)); }
      }
    }
    return chosen.slice(0, n);
  }

  function pickStudy(questions, attempts, size, includeCalc, categoryIds, rng, targetSec, today) {
    rng = rng || Math.random;
    const latest = latestMap(attempts);
    const targetMs = targetSec * 1000;
    const cats = categoryIds && categoryIds.length ? new Set(categoryIds) : null;
    const rows = [];
    for (const q of questions) {
      if (q.pool !== 'study' || partOf(q) !== 'A') continue;
      if (!includeCalc && q.is_calc) continue;
      if (cats && !cats.has(q.category_id)) continue;
      const l = latest.get(q.id);
      rows.push({ id: q.id, category_id: q.category_id, is_correct: l ? okInt(l) : null,
        time_ms: l ? l.time_ms : null, answered_at: l ? l.answered_at : null });
    }
    const wrong = [], unseen = [], slow = [], mastered = [];
    for (const r of rows) {
      if (r.is_correct === null) unseen.push(r);
      else if (!r.is_correct) wrong.push(r);
      else if (r.time_ms > targetMs) slow.push(r);
      else mastered.push(r);
    }
    const stat = new Map();
    for (const r of rows) {
      if (r.is_correct !== null) {
        const s = stat.get(r.category_id) || [0, 0];
        s[0] += r.is_correct; s[1] += 1;
        stat.set(r.category_id, s);
      }
    }
    const weight = (r) => {
      const s = stat.get(r.category_id);
      const acc = s ? s[0] / s[1] : 0.5;
      return 1.0 + (1.0 - acc);
    };
    const n = Math.min(size, rows.length);
    shuffle(wrong, rng);
    const wsorted = wrong.map((r, i) => [r, i]).sort((a, b) =>
      (dayOf(a[0].answered_at) === today) - (dayOf(b[0].answered_at) === today) || a[1] - b[1]).map((x) => x[0]);
    shuffle(slow, rng);
    mastered.sort((a, b) => cmpScalar(a.answered_at || '', b.answered_at || ''));
    const unseenOrder = takeWeighted(unseen, weight, unseen.length, rng);
    const chosen = fillByQuota({ wrong: wsorted, unseen: unseenOrder, slow, mastered }, size, n, (r) => r.id).map((r) => r.id);
    return shuffle(chosen, rng);
  }

  function reviewEntries(questions, attempts, partWanted, categoryIds) {
    const latest = latestMap(attempts);
    const cats = categoryIds && categoryIds.length ? new Set(categoryIds) : null;
    const out = [];
    for (const q of questions) {
      if (q.pool !== 'study' || partOf(q) !== partWanted) continue;
      if (cats && !cats.has(q.category_id)) continue;
      const l = latest.get(q.id);
      if (l && !l.is_correct) out.push({ q, l });
    }
    out.sort((a, b) => cmpScalar(b.l.answered_at || '', a.l.answered_at || '') || b.l.id - a.l.id);
    return out;
  }
  const reviewIds = (questions, attempts, categoryIds) => reviewEntries(questions, attempts, 'A', categoryIds).map((e) => e.q.id);
  const reviewIdsB = (questions, attempts) => reviewEntries(questions, attempts, 'B', null).map((e) => e.q.id);

  function pickReview(questions, attempts, size, categoryIds, rng) {
    const ids = reviewIds(questions, attempts, categoryIds);
    if (rng) shuffle(ids, rng);
    return size ? ids.slice(0, size) : ids;
  }

  function pickMock(questions, examCode, size, rng) {
    rng = rng || Math.random;
    const pool = questions.filter((q) => q.pool === 'mock' && partOf(q) === 'A');
    if (examCode) {
      return pool.filter((q) => q.exam_code === examCode)
        .map((q, i) => [q, i]).sort((a, b) => a[0].number - b[0].number || a[1] - b[1]).map((x) => x[0].id);
    }
    const ids = pool.map((q) => q.id);
    shuffle(ids, rng);
    return ids.slice(0, size === undefined || size === null ? 20 : size);
  }
  const mockTimeLimit = (n) => n * 90;

  /* ---- 科目B ---- */
  const unitKey = (r) => (r.group_id ? 'g:' + r.group_id : 'q:' + r.id);
  function flatten(units, keys) {
    const out = [];
    for (const k of keys) {
      const rs = units.get(k).slice().sort((a, b) =>
        cmpList(nat(a.sub_label), nat(b.sub_label)) || a.number - b.number || cmpScalar(a.id, b.id));
      for (const r of rs) out.push(r.id);
    }
    return out;
  }
  function groupUnits(rows) {
    const units = new Map();
    for (const r of rows) {
      const k = unitKey(r);
      if (!units.has(k)) units.set(k, []);
      units.get(k).push(r);
    }
    return units;
  }

  function pickStudyB(questions, attempts, size, topics, rng, targetSec, today) {
    rng = rng || Math.random;
    const latest = latestMap(attempts);
    const targetMs = targetSec * 1000;
    const tset = topics && topics.length ? new Set(topics) : null;
    const rows = [];
    for (const q of questions) {
      if (q.pool !== 'study' || partOf(q) !== 'B') continue;
      if (tset && !tset.has(q.b_topic)) continue;
      const l = latest.get(q.id);
      rows.push({ id: q.id, number: q.number, group_id: q.group_id, sub_label: q.sub_label, b_topic: q.b_topic,
        is_correct: l ? okInt(l) : null, time_ms: l ? l.time_ms : null, answered_at: l ? l.answered_at : null });
    }
    const units = groupUnits(rows);
    const stat = new Map();
    for (const r of rows) {
      if (r.is_correct !== null) {
        const s = stat.get(r.b_topic) || [0, 0];
        s[0] += r.is_correct; s[1] += 1;
        stat.set(r.b_topic, s);
      }
    }
    const weight = (k) => {
      const s = stat.get(units.get(k)[0].b_topic);
      return 1.0 + (1.0 - (s ? s[0] / s[1] : 0.5));
    };
    const wrong = [], unseen = [], slow = [], mastered = [];
    const last = new Map();
    for (const [k, rs] of units) {
      let mx = '';
      for (const r of rs) { const a = r.answered_at || ''; if (a > mx) mx = a; }
      last.set(k, mx);
      if (rs.some((r) => r.is_correct !== null && r.is_correct === 0)) wrong.push(k);
      else if (rs.some((r) => r.is_correct === null)) unseen.push(k);
      else if (rs.some((r) => r.time_ms > targetMs)) slow.push(k);
      else mastered.push(k);
    }
    const n = Math.min(size, units.size);
    shuffle(wrong, rng);
    const wsorted = wrong.map((k, i) => [k, i]).sort((a, b) =>
      (dayOf(last.get(a[0])) === today) - (dayOf(last.get(b[0])) === today) || a[1] - b[1]).map((x) => x[0]);
    shuffle(slow, rng);
    mastered.sort((a, b) => cmpScalar(last.get(a), last.get(b)));
    const chosen = fillByQuota({ wrong: wsorted, unseen: takeWeighted(unseen, weight, unseen.length, rng), slow, mastered },
      size, n, (k) => k);
    shuffle(chosen, rng);
    return flatten(units, chosen);
  }

  function pickReviewB(questions, attempts, size, rng) {
    const ids = new Set(reviewIdsB(questions, attempts));
    if (!ids.size) return [];
    const units = groupUnits(questions.filter((q) => ids.has(q.id)));
    let keys = Array.from(units.keys());
    if (rng) shuffle(keys, rng);
    if (size) keys = keys.slice(0, size);
    return flatten(units, keys);
  }

  function pickMockB(questions, examCode, size, rng) {
    rng = rng || Math.random;
    const pool = questions.filter((q) => q.pool === 'mock' && partOf(q) === 'B');
    if (examCode) {
      return pool.filter((q) => q.exam_code === examCode)
        .sort((a, b) => a.number - b.number || cmpList(nat(a.sub_label), nat(b.sub_label)) || cmpScalar(a.id, b.id))
        .map((q) => q.id);
    }
    const units = groupUnits(pool);
    const keys = Array.from(units.keys());
    shuffle(keys, rng);
    const out = [];
    let cnt = 0;
    for (const k of keys) {
      if (cnt + units.get(k).length > size) continue;
      out.push(k);
      cnt += units.get(k).length;
    }
    return flatten(units, out);
  }
  const mockTimeLimitB = (n) => n * 300;

  /* ================= セッション作成の計画（POST /api/sessions の前半） ================= */
  function planSession(d, settings, questions, attempts, rng, now) {
    const mode = d.mode;
    if (!['study', 'review', 'mock'].includes(mode)) throw new ApiError('mode は study/review/mock です');
    const part = 'part' in d ? d.part : 'A';
    if (part !== 'A' && part !== 'B') throw new ApiError('part は A/B です');
    let size = d.size === undefined ? null : d.size;
    if (size !== null && (!isInt(size) || size < 1 || size > 100)) throw new ApiError('size が不正です');
    const catIds = 'category_ids' in d ? validateCategoryIds(d.category_ids) : settings.category_ids;
    const today = todayOf(now);
    let ids, opts, limit = null;
    if (part === 'B') {
      const topics = 'b_topics' in d ? validateBTopics(d.b_topics) : settings.b_topics;
      if (mode === 'study') {
        size = size || settings.session_size_b;
        ids = pickStudyB(questions, attempts, size, topics, rng, settings.target_sec_b, today);
        opts = { part: 'B', size, b_topics: topics };
      } else if (mode === 'review') {
        ids = pickReviewB(questions, attempts, size, rng);
        opts = { part: 'B', size };
      } else {
        const ec = d.exam_code === undefined ? null : d.exam_code;
        if (ec !== null && !isStr(ec)) throw new ApiError('exam_code が不正です');
        ids = pickMockB(questions, ec, size || B_MOCK_MAX, rng);
        limit = mockTimeLimitB(ids.length);
        opts = { part: 'B', exam_code: ec, size: ids.length };
      }
    } else if (mode === 'study') {
      size = size || settings.session_size;
      ids = pickStudy(questions, attempts, size, settings.include_calc, catIds, rng, settings.target_sec, today);
      opts = { size, include_calc: settings.include_calc, category_ids: catIds };
    } else if (mode === 'review') {
      ids = pickReview(questions, attempts, size, catIds, rng);
      opts = { size, category_ids: catIds };
    } else {
      const ec = d.exam_code === undefined ? null : d.exam_code;
      if (ec !== null && !isStr(ec)) throw new ApiError('exam_code が不正です');
      ids = pickMock(questions, ec, size || 20, rng);
      limit = mockTimeLimit(ids.length);
      opts = { exam_code: ec, size: ids.length };
    }
    if (!ids.length) throw new ApiError('出題できる問題がありません', 409);
    return { mode, ids, opts, limit };
  }

  /* ================= セッション / 問題の整形（api.py） ================= */
  function pathList(v) {
    if (typeof v === 'string') { try { v = JSON.parse(v); } catch (e) { v = []; } }
    return Array.isArray(v) ? v.filter((p) => isStr(p) && p) : [];
  }
  const urlsOf = (paths, url) => paths.map((p) => url(p)).filter(Boolean);

  function bFields(q, url) {
    let paths = pathList(q.images);
    if (!paths.length && q.image_path) paths = [q.image_path];
    return {
      part: partOf(q), image_urls: urlsOf(paths, url), sub_label: q.sub_label == null ? null : q.sub_label,
      group_id: q.group_id == null ? null : q.group_id, b_topic: q.b_topic == null ? null : q.b_topic,
      tags: Array.isArray(q.tags) ? q.tags : [], difficulty: q.difficulty == null ? null : q.difficulty,
      has_book_explanation: urlsOf(pathList(q.explanation_images), url).length > 0,
    };
  }
  function explanationFields(q, url) {
    return { explanation_image_urls: urlsOf(pathList(q.explanation_images), url),
      explanation_source: q.explanation_source == null ? null : q.explanation_source };
  }
  /** この問題の表示に使う画像パス（URL 解決の事前取得用） */
  function mediaPathsOf(q) {
    const out = [];
    if (q.image_path) out.push(q.image_path);
    out.push(...pathList(q.images), ...pathList(q.explanation_images));
    return out;
  }
  function questionBase(q, url) {
    return {
      id: q.id, exam_label: q.exam_label, number: q.number, category_id: q.category_id,
      category_name: catName(q.category_id), body: q.body, choices: q.choices,
      image_url: q.image_path ? (url(q.image_path) || null) : null,
      is_calc: !!q.is_calc, source_label: q.source_label, explanation_url: q.explanation_url,
    };
  }
  const revealOk = (s) => s.mode !== 'mock' || s.status === 'done';

  function sessionObject(s, qById, sessAttempts, url) {
    const atts = new Map();
    for (const a of sessAttempts) if (a.chosen != null) atts.set(a.question_id, a);
    const reveal = revealOk(s);
    const questions = [];
    for (const qid of s.question_ids) {
      const q = qById.get(qid);
      if (!q) continue;
      const a = atts.get(qid);
      let att = null;
      if (a) {
        att = { chosen: a.chosen, time_ms: a.time_ms };
        if (reveal) {
          att.is_correct = !!a.is_correct;
          att.answer = q.answer;
          if (partOf(q) === 'B') Object.assign(att, explanationFields(q, url));
        }
      }
      questions.push(Object.assign({}, bFields(q, url), questionBase(q, url), { attempt: att }));
    }
    let remaining = null;
    if (s.time_limit_sec !== null && s.time_limit_sec !== undefined) {
      const used = sessAttempts.reduce((t, a) => t + a.time_ms, 0) + s.current_elapsed_ms;
      remaining = Math.max(0, s.time_limit_sec - Math.floor(used / 1000));
    }
    return {
      id: s.id, mode: s.mode, part: questions.length ? questions[0].part : 'A', status: s.status,
      current_index: s.current_index, current_elapsed_ms: s.current_elapsed_ms,
      time_limit_sec: s.time_limit_sec === undefined ? null : s.time_limit_sec, started_at: s.started_at,
      remaining_sec: remaining, questions,
    };
  }

  /** POST /api/sessions/{id}/finish の返り値。obj は sessionObject(...)（finish 後の状態） */
  function finishResult(obj, s, sessAttempts) {
    const total = obj.questions.length;
    const correct = obj.questions.filter((q) => q.attempt && q.attempt.is_correct).length;
    const timeMs = sessAttempts.reduce((t, a) => t + a.time_ms, 0);
    const cats = new Map();
    for (const q of obj.questions) {
      if (!cats.has(q.category_id)) cats.set(q.category_id, { category_id: q.category_id, name: q.category_name, total: 0, correct: 0 });
      const c = cats.get(q.category_id);
      c.total += 1;
      c.correct += q.attempt && q.attempt.is_correct ? 1 : 0;
    }
    return {
      total, correct, time_ms: timeMs, avg_time_ms: total ? Math.floor(timeMs / total) : 0,
      by_category: Array.from(cats.values()).sort((a, b) => a.category_id - b.category_id),
      questions: obj.questions,
      passed: s.mode === 'mock' && total ? correct / total >= 0.6 : null,
    };
  }

  function questionDetail(q, sessions, attempts, url) {
    const sById = new Map(sessions.map((s) => [s.id, s]));
    const reveal = q.pool === 'study' || sessions.some((s) =>
      s.status === 'done' && s.mode === 'mock' && s.question_ids.includes(q.id));
    const atts = !reveal ? [] : attempts
      .filter((a) => a.question_id === q.id && a.chosen != null && sById.has(a.session_id) &&
        (sById.get(a.session_id).mode !== 'mock' || sById.get(a.session_id).status === 'done'))
      .sort((a, b) => b.id - a.id);
    const extra = bFields(q, url);
    if (reveal && partOf(q) === 'B') Object.assign(extra, explanationFields(q, url));
    return Object.assign({}, extra, questionBase(q, url), {
      pool: q.pool,
      answer: reveal ? q.answer : null,
      attempts: atts.map((a) => ({ chosen: a.chosen, is_correct: !!a.is_correct, time_ms: a.time_ms,
        answered_at: a.answered_at, mode: sById.get(a.session_id).mode })),
    });
  }

  /* ================= 集計（stats.py） ================= */
  function todaySummary(attempts, today) {
    let n = 0, c = 0, t = 0;
    for (const a of attempts) {
      if (a.chosen == null || dayOf(a.answered_at) !== today) continue;
      n += 1; c += okInt(a); t += a.time_ms;
    }
    return { answered: n, correct: c, time_ms: t };
  }

  function streakDays(attempts, today) {
    const days = new Set();
    for (const a of attempts) if (a.chosen != null) days.add(dayOf(a.answered_at));
    let d = today;
    if (!days.has(d)) d = addDays(d, -1);
    let n = 0;
    while (days.has(d)) { n += 1; d = addDays(d, -1); }
    return n;
  }

  function categoryStats(questions, attempts, settings, part) {
    part = part || 'A';
    const targetMs = (part === 'B' ? settings.target_sec_b : settings.target_sec) * 1000;
    const qs = questions.filter((q) => q.pool === 'study' && partOf(q) === part);
    const qById = questionMap(qs);
    const total = new Map();
    for (const q of qs) total.set(q.category_id, (total.get(q.category_id) || 0) + 1);
    const tot = new Map();
    for (const a of attempts) {
      if (a.chosen == null) continue;
      const q = qById.get(a.question_id);
      if (!q) continue;
      const t = tot.get(q.category_id) || { attempts: 0, correct: 0, sum: 0 };
      t.attempts += 1; t.correct += okInt(a); t.sum += a.time_ms;
      tot.set(q.category_id, t);
    }
    const lat = new Map();
    for (const [qid, l] of latestMap(attempts)) {
      const q = qById.get(qid);
      if (!q) continue;
      const g = lat.get(q.category_id) || { seen: 0, ok: 0, mastered: 0 };
      g.seen += 1; g.ok += okInt(l);
      if (l.is_correct && l.time_ms <= targetMs) g.mastered += 1;
      lat.set(q.category_id, g);
    }
    const fields = {};
    for (const c of CATS) {
      const t = tot.get(c.id), l = lat.get(c.id);
      const seen = l ? l.seen : 0;
      (fields[c.field] = fields[c.field] || []).push({
        id: c.id, name: c.name, major_no: c.major_no, major_name: c.major_name,
        total: total.get(c.id) || 0, seen,
        attempts: t ? t.attempts : 0, correct: t ? t.correct : 0,
        accuracy: seen ? round4(l.ok / seen) : null,
        avg_time_ms: t ? Math.trunc(t.sum / t.attempts) : null,
        mastered: l ? l.mastered : 0,
      });
    }
    return { fields: ['te', 'ma', 'st'].filter((f) => fields[f]).map((f) => ({ field: f, name: FIELD_NAMES[f], categories: fields[f] })) };
  }

  function weakCategories(questions, attempts, settings, limit) {
    const out = [];
    for (const f of categoryStats(questions, attempts, settings, 'A').fields) {
      for (const c of f.categories) if (c.accuracy !== null) out.push({ id: c.id, name: c.name, accuracy: c.accuracy });
    }
    out.sort((a, b) => a.accuracy - b.accuracy || a.id - b.id);
    return out.slice(0, limit === undefined ? 3 : limit);
  }

  function timeStats(questions, attempts, settings, part, today) {
    part = part || 'all';
    const target = { A: settings.target_sec * 1000, B: settings.target_sec_b * 1000 };
    const qById = questionMap(questions);
    const rows = [];
    for (const a of attempts.slice().sort((x, y) => x.id - y.id)) {
      if (a.chosen == null) continue;
      const q = qById.get(a.question_id);
      if (!q || !CATEGORY_BY_ID.has(q.category_id)) continue;
      if (part !== 'all' && partOf(q) !== part) continue;
      rows.push({ question_id: q.id, is_correct: okInt(a), time_ms: a.time_ms, d: dayOf(a.answered_at),
        category_id: q.category_id, cname: catName(q.category_id), is_calc: !!q.is_calc, label: labelOf(q), part: partOf(q) });
    }
    const times = rows.map((r) => r.time_ms);
    const overall = {
      avg_ms: avgInt(times),
      median_ms: times.length ? Math.trunc(median(times)) : null,
      over_target_rate: rows.length ? round4(rows.filter((r) => r.time_ms > target[r.part]).length / rows.length) : null,
    };
    const bycat = new Map();
    for (const r of rows) { if (!bycat.has(r.category_id)) bycat.set(r.category_id, []); bycat.get(r.category_id).push(r); }
    const byCategory = Array.from(bycat.keys()).sort((a, b) => a - b).map((cid) => {
      const rs = bycat.get(cid), ts = rs.map((x) => x.time_ms);
      return { id: cid, name: rs[0].cname, avg_ms: avgInt(ts), median_ms: Math.trunc(median(ts)), n: rs.length };
    });
    const grp = (rs) => ({
      avg_ms: avgInt(rs.map((x) => x.time_ms)),
      accuracy: rs.length ? round4(rs.reduce((t, x) => t + x.is_correct, 0) / rs.length) : null,
      n: rs.length,
    });
    const since = addDays(today, -29);
    const byday = new Map();
    for (const r of rows) {
      if (r.d >= since) { if (!byday.has(r.d)) byday.set(r.d, []); byday.get(r.d).push(r); }
    }
    const daily = Array.from(byday.keys()).sort().map((d) => {
      const rs = byday.get(d);
      return { date: d, n: rs.length, avg_ms: avgInt(rs.map((x) => x.time_ms)),
        accuracy: round4(rs.reduce((t, x) => t + x.is_correct, 0) / rs.length) };
    });
    const slowest = rows.slice().sort((a, b) => b.time_ms - a.time_ms).slice(0, 10).map((r) => ({
      question_id: r.question_id, label: r.label, category_name: r.cname, time_ms: r.time_ms, is_correct: !!r.is_correct }));
    const buckets = [['〜30秒', 0, 30000], ['30〜60秒', 30000, 60000], ['60〜90秒', 60000, 90000],
      ['90〜120秒', 90000, 120000], ['120秒〜', 120000, null]];
    const speed = buckets.map(([name, lo, hi]) => {
      const rs = rows.filter((r) => r.time_ms >= lo && (hi === null || r.time_ms < hi));
      return { bucket: name, n: rs.length,
        accuracy: rs.length ? round4(rs.reduce((t, r) => t + r.is_correct, 0) / rs.length) : null };
    });
    return { overall, by_category: byCategory,
      calc_vs_noncalc: { calc: grp(rows.filter((r) => r.is_calc)), noncalc: grp(rows.filter((r) => !r.is_calc)) },
      daily, slowest, speed_accuracy: speed };
  }

  function bStats(questions, attempts) {
    const latest = latestMap(attempts);
    const nAtt = new Map(), sumMs = new Map();
    for (const a of attempts) {
      if (a.chosen == null) continue;
      nAtt.set(a.question_id, (nAtt.get(a.question_id) || 0) + 1);
      sumMs.set(a.question_id, (sumMs.get(a.question_id) || 0) + a.time_ms);
    }
    const rows = questions.filter((q) => q.pool === 'study' && partOf(q) === 'B').map((q) => {
      const l = latest.get(q.id);
      return { b_topic: q.b_topic, tags: q.tags, difficulty: q.difficulty, is_correct: l ? okInt(l) : null,
        n_att: nAtt.get(q.id) || 0, sum_ms: sumMs.get(q.id) || 0 };
    });
    const agg = (rs) => {
      const seen = rs.filter((r) => r.is_correct !== null);
      const n = rs.reduce((t, r) => t + r.n_att, 0);
      return { total: rs.length, seen: seen.length,
        accuracy: seen.length ? round4(seen.reduce((t, r) => t + r.is_correct, 0) / seen.length) : null,
        avg_time_ms: n ? Math.trunc(rs.reduce((t, r) => t + r.sum_ms, 0) / n) : null };
    };
    const byTopic = new Map(), byTag = new Map(), byDiff = new Map();
    const push = (m, k, r) => { if (!m.has(k)) m.set(k, []); m.get(k).push(r); };
    for (const r of rows) {
      push(byTopic, r.b_topic == null ? null : r.b_topic, r);
      if (r.difficulty) push(byDiff, r.difficulty, r);
      const tags = Array.isArray(r.tags) ? r.tags : [];
      for (const t of new Set(tags.filter(isStr))) push(byTag, t, r);
    }
    const topicKeys = Object.keys(B_TOPIC_NAMES).concat(Array.from(byTopic.keys()).filter((k) => k && !(k in B_TOPIC_NAMES)));
    const topics = topicKeys.map((k) => Object.assign({ b_topic: k, name: B_TOPIC_NAMES[k] || k }, agg(byTopic.get(k) || [])));
    const tags = Array.from(byTag.entries()).sort((a, b) => b[1].length - a[1].length || cmpScalar(a[0], b[0]))
      .map(([t, rs]) => Object.assign({ tag: t }, agg(rs)));
    const order = DIFFICULTIES.concat(Array.from(byDiff.keys()).filter((k) => !DIFFICULTIES.includes(k)).sort());
    const diff = order.filter((k) => byDiff.has(k)).map((k) => Object.assign({ difficulty: k }, agg(byDiff.get(k))));
    return { topics, tags, difficulty: diff };
  }

  /** GET /api/history。limit / offset は検証済みの整数 */
  function historyList(questions, sessions, attempts, limit, offset) {
    const qById = questionMap(questions), sById = new Map(sessions.map((s) => [s.id, s]));
    const rows = attempts.filter((a) => {
      if (a.chosen == null) return false;
      const s = sById.get(a.session_id), q = qById.get(a.question_id);
      return s && q && CATEGORY_BY_ID.has(q.category_id) && (s.mode !== 'mock' || s.status === 'done');
    }).sort((a, b) => b.id - a.id).slice(offset, offset + limit);
    return rows.map((a) => {
      const q = qById.get(a.question_id);
      return { question_id: a.question_id, part: partOf(q), label: labelOf(q), category_name: catName(q.category_id),
        chosen: a.chosen, answer: q.answer, is_correct: !!a.is_correct, time_ms: a.time_ms,
        answered_at: a.answered_at, mode: sById.get(a.session_id).mode };
    });
  }

  /** GET /api/home */
  function homeData(questions, sessions, attempts, settings, now) {
    const today = todayOf(now);
    let s = null;
    for (const x of sessions) if (x.status === 'active' && (!s || x.id > s.id)) s = x;
    let active = null;
    if (s) {
      const first = s.question_ids.length ? questions.find((q) => q.id === s.question_ids[0]) : null;
      active = { id: s.id, mode: s.mode, part: first ? partOf(first) : 'A', current_index: s.current_index,
        total: s.question_ids.length };
    }
    const answered = new Set();
    for (const a of attempts) if (a.chosen != null) answered.add(a.question_id);
    const poolOf = (part, reviewN) => ({
      study: questions.filter((q) => q.pool === 'study' && partOf(q) === part).length,
      study_unseen: questions.filter((q) => q.pool === 'study' && partOf(q) === part && !answered.has(q.id)).length,
      mock: questions.filter((q) => q.pool === 'mock' && partOf(q) === part).length,
      review: reviewN,
    });
    const exams = new Map();
    for (const q of questions) {
      if (q.pool !== 'mock') continue;
      const k = JSON.stringify([partOf(q), q.exam_code, q.exam_label]);
      if (!exams.has(k)) exams.set(k, { exam_code: q.exam_code, exam_label: q.exam_label, part: partOf(q), count: 0 });
      exams.get(k).count += 1;
    }
    const mockExams = Array.from(exams.values()).sort((a, b) =>
      cmpScalar(b.exam_code, a.exam_code) || cmpScalar(a.part, b.part) || cmpScalar(a.exam_label, b.exam_label));
    return {
      active_session: active, today: todaySummary(attempts, today), mock_exams: mockExams,
      streak_days: streakDays(attempts, today), settings,
      pool: poolOf('A', reviewIds(questions, attempts).length),
      pool_b: poolOf('B', reviewIdsB(questions, attempts).length),
      weak_categories: weakCategories(questions, attempts, settings),
    };
  }

  /** history の limit / offset（Python の int() と同じ: 空文字や小数は 400） */
  function parseHistoryParams(params) {
    const toInt = (v, def) => {
      if (v === null || v === undefined) return def;
      if (!/^\s*[+-]?\d+\s*$/.test(v)) throw new ApiError('limit/offset が不正です');
      return parseInt(v, 10);
    };
    const limit = Math.min(Math.max(toInt(params.get('limit'), 50), 1), 500);
    const offset = Math.max(toInt(params.get('offset'), 0), 0);
    return { limit, offset };
  }

  return {
    CATEGORIES, setCategories, FIELD_NAMES, DEFAULT_SETTINGS, LABELS, LABELS_B, B_TOPICS, B_TOPIC_NAMES, DIFFICULTIES, B_MOCK_MAX,
    ApiError, isInt, pyRound, round4, median, shuffle, nat, addDays, todayOf, latestMap,
    validateCategoryIds, validateBTopics, validateSettings, mergeSettings,
    pickStudy, pickReview, reviewIds, pickMock, mockTimeLimit,
    pickStudyB, pickReviewB, reviewIdsB, pickMockB, mockTimeLimitB, planSession,
    sessionObject, finishResult, questionDetail, mediaPathsOf, bFields, explanationFields,
    todaySummary, streakDays, categoryStats, weakCategories, timeStats, bStats, historyList, homeData,
    parseHistoryParams,
  };
});
