'use strict';
// テスト共通: conftest.py と同じ固定データ（A 69 問 + mock 10、B study 8 + mock 3）と API ヘルパー
const path = require('path');
const LOCAL = path.join(__dirname, '..', '..', 'static', 'local');
const L = require(path.join(LOCAL, 'logic.js'));
const FEStore = require(path.join(LOCAL, 'store.js'));
const FELocal = require(path.join(LOCAL, 'api-local.js'));

const CALC = new Set(['2,3', '3,3', '9,3']);
const LA = Array.from('アイウエ');
const LB = Array.from('アイウエオカキクケコ');
const pad2 = (n) => String(n).padStart(2, '0');

function qA(id, exam, label, num, cat, calc, pool) {
  return { id, exam_code: exam, exam_label: label, number: num, category_id: cat, body: `本文 ${id}`,
    choices: LA.map((l) => ({ label: l, text: `選択肢${l}` })), answer: 'ア', image_path: num === 1 ? 'x/q.png' : null,
    is_calc: !!calc, pool, source_label: '出典', source_url: 'http://src', explanation_url: `http://exp/${id}`,
    part: 'A', images: null, group_id: null, sub_label: null, b_topic: null, tags: null, difficulty: null,
    explanation_images: null, explanation_source: null };
}
function qB(id, exam, label, num, topic, pool, o = {}) {
  const expl = o.expl !== false;
  return { id, exam_code: exam, exam_label: label, number: num, category_id: topic === 'pseudo' ? 2 : 11,
    body: `【テスト】B問題 ${id}`, choices: LB.map((l, i) => ({ label: l, text: i > 3 ? '' : `択${l}` })),
    answer: o.answer || 'ア', image_path: null, is_calc: false, pool, source_label: '【テスト】出典',
    source_url: 'http://src', explanation_url: '', part: 'B', images: [`b/${id}-1.png`, `b/${id}-2.png`],
    group_id: o.group || null, sub_label: o.sub || null, b_topic: topic, tags: o.tags && o.tags.length ? o.tags : null,
    difficulty: o.diff || null, explanation_images: expl ? [`bx/${id}-e1.png`] : null,
    explanation_source: expl ? '【テスト】解説出典' : null };
}

function fixtureQuestions() {
  const qs = [];
  for (let cid = 1; cid <= 23; cid++) {
    for (let i = 1; i <= 3; i++) {
      qs.push(qA(`fe-30_aki-q${pad2(cid)}${i}`, '30_aki', '平成30年秋期', cid * 10 + i, cid, CALC.has(`${cid},${i}`), 'study'));
    }
  }
  for (let n = 1; n <= 10; n++) qs.push(qA(`fe-05_haru-q${pad2(n)}`, '05_haru', '令和5年春期', n, ((n - 1) % 23) + 1, false, 'mock'));
  qs.push(qB('feb-sample-q01', 'sample', 'サンプル', 1, 'pseudo', 'study', { tags: ['トレース', '再帰'], diff: 'やさしい' }));
  qs.push(qB('feb-sample-q02', 'sample', 'サンプル', 2, 'pseudo', 'study', { tags: ['トレース'], diff: 'ふつう', answer: 'コ' }));
  qs.push(qB('feb-sample-q03', 'sample', 'サンプル', 3, 'pseudo', 'study', { tags: ['一次元配列'], diff: '難しい', expl: false }));
  qs.push(qB('feb-sample-q04', 'sample', 'サンプル', 4, 'security', 'study', { tags: ['暗号'], diff: 'ふつう' }));
  qs.push(qB('feb-sample-q05', 'sample', 'サンプル', 5, 'security', 'study', { tags: ['暗号', '認証'] }));
  for (const [i, sub] of [[3, '設問3(2)'], [1, '設問1'], [2, '設問2']]) {
    qs.push(qB(`feb-furoku3-q01-${i}`, 'furoku3', '付録3', 1, 'security', 'study', { group: 'furoku3-q01', sub, tags: ['認証'], diff: '難しい' }));
  }
  for (let n = 1; n <= 3; n++) qs.push(qB(`feb-05_haru-q${pad2(n)}`, '05_haru', '令和5年', n, 'pseudo', 'mock', { tags: ['トレース'] }));
  return qs;
}

/** mulberry32: 再現可能な乱数 */
function seeded(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** now は可変（clock.set('2026-11-26 10:00:00')）。mediaUrl は '/media/' + path */
async function makeEnv(opts = {}) {
  const store = FEStore.memory();
  await store.putMany('questions', opts.questions || fixtureQuestions());
  const clock = { v: opts.now || '2026-11-26 10:00:00', set(x) { this.v = x; } };
  FELocal.init({ store, rng: opts.rng || seeded(1234), now: () => clock.v, mediaUrl: (p) => `/media/${p}` });
  const api = (m, p, b) => FELocal.api(m, p, b);
  const err = async (promise) => {
    try { await promise; } catch (e) { return e; }
    throw new Error('エラーになるはずが成功した');
  };
  const start = (body) => api('POST', '/api/sessions', body);
  const answerAll = async (sess, chosen = 'ア', timeMs = 10000) => {
    for (const q of sess.questions) await api('POST', `/api/sessions/${sess.id}/answer`, { question_id: q.id, chosen, time_ms: timeMs });
  };
  return { store, clock, api, err, start, answerAll, FELocal };
}

/** 指定の過去 attempt を直接投入（stats テスト用）。session は done の study */
async function addAttempt(store, qid, chosen, ok, timeMs, at, mode = 'study') {
  const sid = await store.put('sessions', { mode, status: 'done', question_ids: [], current_index: 0, current_elapsed_ms: 0,
    time_limit_sec: null, options: {}, started_at: at, updated_at: at, ended_at: at });
  await store.put('attempts', { session_id: sid, question_id: qid, chosen, is_correct: ok, time_ms: timeMs, answered_at: at });
  return sid;
}

module.exports = { L, FEStore, FELocal, fixtureQuestions, seeded, makeEnv, addAttempt, qA, qB };
