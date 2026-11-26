'use strict';
// tests/test_api.py, test_selection.py, test_stats.py の JS 移植（ローカル API 版）
const test = require('node:test');
const assert = require('node:assert/strict');
const { makeEnv, addAttempt, seeded, L } = require('./helpers.js');

const NOW = '2026-11-26 10:00:00';

test('home: 空の状態', async () => {
  const { api } = await makeEnv();
  const h = await api('GET', '/api/home');
  assert.equal(h.active_session, null);
  assert.deepEqual(h.pool, { study: 69, study_unseen: 69, mock: 10, review: 0 });
  assert.deepEqual(h.pool_b, { study: 8, study_unseen: 8, mock: 3, review: 0 });
  assert.deepEqual(h.today, { answered: 0, correct: 0, time_ms: 0 });
  assert.equal(h.streak_days, 0);
  assert.equal(h.settings.session_size, 8);
  assert.deepEqual(h.mock_exams.map((e) => [e.part, e.exam_code, e.count]), [['A', '05_haru', 10], ['B', '05_haru', 3]]);
  assert.deepEqual(Object.keys(h), ['active_session', 'today', 'mock_exams', 'streak_days', 'settings', 'pool', 'pool_b', 'weak_categories']);
});

test('セッションの一連の流れ（回答・上書き・finish・オートセーブ）', async () => {
  const { api, start } = await makeEnv();
  const s = await start({ mode: 'study' });
  assert.equal(s.questions.length, 8);
  assert.equal(s.status, 'active');
  const q = s.questions[0];
  assert.equal(q.attempt, null);
  let r = await api('POST', `/api/sessions/${s.id}/answer`, { question_id: q.id, chosen: 'イ', time_ms: 5000 });
  assert.deepEqual(r, { is_correct: false, answer: 'ア', explanation_url: `http://exp/${q.id}` });
  r = await api('POST', `/api/sessions/${s.id}/answer`, { question_id: q.id, chosen: 'ア', time_ms: 6000 });
  assert.equal(r.is_correct, true);
  const cur = await api('GET', '/api/sessions/active');
  assert.equal(cur.current_index, 1);
  assert.deepEqual(cur.questions[0].attempt, { chosen: 'ア', is_correct: true, time_ms: 6000, answer: 'ア' });
  assert.deepEqual((await api('GET', '/api/home')).active_session, { id: s.id, mode: 'study', part: 'A', current_index: 1, total: 8 });
  for (const qq of s.questions.slice(1)) await api('POST', `/api/sessions/${s.id}/answer`, { question_id: qq.id, chosen: 'イ', time_ms: 1000 });
  const f = await api('POST', `/api/sessions/${s.id}/finish`);
  assert.equal(f.total, 8); assert.equal(f.correct, 1); assert.equal(f.passed, null);
  assert.equal(f.time_ms, 6000 + 7000);
  assert.equal(f.avg_time_ms, Math.floor(13000 / 8));
  assert.equal(f.by_category.reduce((t, c) => t + c.total, 0), 8);
  assert.equal(await api('GET', '/api/sessions/active'), null);
  const h = await api('GET', '/api/home');
  assert.equal(h.today.answered, 8); assert.equal(h.streak_days, 1);
});

test('回答の検証と 404/409', async () => {
  const { api, err, start } = await makeEnv();
  const s = await start({ mode: 'study' });
  const url = `/api/sessions/${s.id}/answer`;
  assert.equal((await err(api('POST', url, { question_id: 'nope', chosen: 'ア', time_ms: 1 }))).status, 400);
  const qid = s.questions[0].id;
  assert.equal((await err(api('POST', url, { question_id: qid, chosen: 'Z', time_ms: 1 }))).status, 400);
  assert.equal((await err(api('POST', url, { question_id: qid, chosen: 'ア', time_ms: -1 }))).status, 400);
  assert.equal((await err(api('POST', url, { question_id: qid, chosen: 'ア', time_ms: 1.5 }))).status, 400);
  assert.equal((await err(api('POST', url, [1]))).status, 400);
  assert.equal((await err(api('GET', '/api/sessions/9999'))).status, 404);
  assert.equal((await err(api('POST', '/api/sessions/9999/finish'))).status, 404);
  // A に B 用ラベルは不可
  assert.equal((await err(api('POST', url, { question_id: qid, chosen: 'オ', time_ms: 1 }))).status, 400);
  await api('POST', `/api/sessions/${s.id}/finish`);
  const e = await err(api('POST', url, { question_id: qid, chosen: 'ア', time_ms: 1 }));
  assert.equal(e.status, 409); assert.equal(e.error, 'このセッションは終了しています');
  assert.equal((await err(api('GET', '/api/nothing'))).status, 404);
});

test('オートセーブ progress（text/plain 文字列の body も可）', async () => {
  const { api, err, start } = await makeEnv();
  const s = await start({ mode: 'study' });
  assert.deepEqual(await api('POST', `/api/sessions/${s.id}/progress`, { current_index: 2, current_elapsed_ms: 15300 }), { ok: true });
  await api('POST', `/api/sessions/${s.id}/progress`, JSON.stringify({ current_index: 3, current_elapsed_ms: 4200 }));
  const a = await api('GET', '/api/sessions/active');
  assert.equal(a.current_index, 3); assert.equal(a.current_elapsed_ms, 4200);
  assert.equal((await err(api('POST', `/api/sessions/${s.id}/progress`, { current_index: 99, current_elapsed_ms: 0 }))).status, 400);
  assert.equal((await err(api('POST', `/api/sessions/${s.id}/progress`, { current_index: 1, current_elapsed_ms: -1 }))).status, 400);
});

test('新しいセッションは active を abandoned にする', async () => {
  const { api, err, start } = await makeEnv();
  const a = await start({ mode: 'study' });
  const b = await start({ mode: 'study' });
  assert.equal((await api('GET', `/api/sessions/${a.id}`)).status, 'abandoned');
  assert.equal((await api('GET', '/api/sessions/active')).id, b.id);
  const e = await err(api('POST', `/api/sessions/${a.id}/finish`));
  assert.equal(e.status, 409);
  // 中断セッションへの progress は無視される（200）
  await api('POST', `/api/sessions/${a.id}/progress`, { current_index: 1, current_elapsed_ms: 1 });
  assert.equal((await api('GET', `/api/sessions/${a.id}`)).current_index, 0);
});

test('模試は finish まで正誤を出さない', async () => {
  const { api, start } = await makeEnv();
  const s = await start({ mode: 'mock', exam_code: '05_haru' });
  assert.equal(s.questions.length, 10); assert.equal(s.time_limit_sec, 900);
  assert.equal(s.remaining_sec, 900);
  const q = s.questions[0];
  assert.deepEqual(await api('POST', `/api/sessions/${s.id}/answer`, { question_id: q.id, chosen: 'ア', time_ms: 3000 }), { saved: true });
  const cur = await api('GET', `/api/sessions/${s.id}`);
  assert.deepEqual(cur.questions[0].attempt, { chosen: 'ア', time_ms: 3000 });
  assert.equal(cur.remaining_sec, 897);
  assert.equal((await api('GET', `/api/questions/${q.id}`)).answer, null);
  assert.deepEqual(await api('GET', '/api/history'), []);
  const f = await api('POST', `/api/sessions/${s.id}/finish`);
  assert.equal(f.total, 10); assert.equal(f.correct, 1); assert.equal(f.passed, false);
  assert.equal(f.questions[0].attempt.answer, 'ア');
  assert.equal(f.questions[1].attempt, null);
  assert.equal((await api('GET', `/api/questions/${q.id}`)).answer, 'ア');
  assert.equal((await api('GET', '/api/history')).length, 1);
});

test('模試ランダム・合格', async () => {
  const { api, start, answerAll } = await makeEnv();
  const s = await start({ mode: 'mock' });
  assert.equal(s.questions.length, 10);
  await answerAll(s);
  assert.equal((await api('POST', `/api/sessions/${s.id}/finish`)).passed, true);
});

test('復習は最新が不正解の問題のみ', async () => {
  const { api, err, start } = await makeEnv();
  assert.equal((await err(start({ mode: 'review' }))).status, 409);
  const s = await start({ mode: 'study', size: 10 });
  const ids = s.questions.map((q) => q.id);
  for (let i = 0; i < s.questions.length; i++) {
    await api('POST', `/api/sessions/${s.id}/answer`, { question_id: s.questions[i].id, chosen: i < 3 ? 'イ' : 'ア', time_ms: 1000 });
  }
  await api('POST', `/api/sessions/${s.id}/finish`);
  assert.equal((await api('GET', '/api/home')).pool.review, 3);
  const r = await start({ mode: 'review' });
  assert.deepEqual(new Set(r.questions.map((q) => q.id)), new Set(ids.slice(0, 3)));
});

test('今回だけの分野指定', async () => {
  const { api, err, start } = await makeEnv();
  const st = await api('GET', '/api/settings');
  const s = await start({ mode: 'study', category_ids: [5, 6] });
  assert.ok(s.questions.every((q) => [5, 6].includes(q.category_id)));
  assert.deepEqual(await api('GET', '/api/settings'), st);
  for (const bad of [[0], [24], '3', [1.5], [true]]) {
    assert.equal((await err(api('POST', '/api/sessions', { mode: 'study', category_ids: bad }))).status, 400);
  }
  const s2 = await start({ mode: 'study', category_ids: [5] });
  for (const q of s2.questions) await api('POST', `/api/sessions/${s2.id}/answer`, { question_id: q.id, chosen: 'イ', time_ms: 1 });
  await api('POST', `/api/sessions/${s2.id}/finish`);
  const r = await start({ mode: 'review', category_ids: [5] });
  assert.ok(r.questions.every((q) => q.category_id === 5));
  assert.equal((await err(start({ mode: 'review', category_ids: [6] }))).status, 409);
});

test('設定の取得・部分更新・検証', async () => {
  const { api, err, start } = await makeEnv();
  const s = await api('PUT', '/api/settings', { session_size: 10, theme: 'dark', category_ids: [3, 1] });
  assert.equal(s.session_size, 10); assert.equal(s.theme, 'dark'); assert.deepEqual(s.category_ids, [1, 3]);
  assert.equal((await api('GET', '/api/settings')).include_calc, true);
  const bads = [{ session_size: 4 }, { session_size: 16 }, { foo: 1 }, { theme: 'x' }, { font_scale: 2 }, { include_calc: 1 },
    { category_ids: [99] }, { target_sec: 1 }, { session_size: 8, theme: 'x' }, { session_size_b: 0 }, { session_size_b: 5 },
    { b_topics: ['x'] }, { b_topics: 'pseudo' }, { target_sec_b: 5 }, { session_size_b: true }];
  for (const bad of bads) assert.equal((await err(api('PUT', '/api/settings', bad))).status, 400, JSON.stringify(bad));
  // 検証エラーの PUT は何も保存しない
  assert.equal((await api('GET', '/api/settings')).session_size, 10);
  assert.equal((await api('PUT', '/api/settings', { font_scale: 1.15 })).font_scale, 1.15);
  assert.deepEqual(await api('PUT', '/api/settings', { b_topics: ['security', 'pseudo'], session_size_b: 4, target_sec_b: 240 }),
    Object.assign(await api('GET', '/api/settings'), { b_topics: ['pseudo', 'security'] }));
  await api('PUT', '/api/settings', { b_topics: [], session_size_b: 2, target_sec_b: 300 });
  const q = await start({ mode: 'study' });
  assert.equal(q.questions.length, 6);
  assert.ok(q.questions.every((x) => [1, 3].includes(x.category_id)));
  const d = await api('GET', '/api/settings');
  assert.deepEqual(Object.keys(d).sort(), Object.keys(L.DEFAULT_SETTINGS).sort());
});

test('履歴と単問', async () => {
  const { api, err, start, answerAll } = await makeEnv();
  const s = await start({ mode: 'study', size: 5 });
  await answerAll(s);
  const h = await api('GET', '/api/history?limit=3');
  assert.equal(h.length, 3); assert.equal(h[0].answer, 'ア'); assert.equal(h[0].mode, 'study');
  assert.equal(h[0].question_id, s.questions[4].id);
  assert.equal((await api('GET', '/api/history?limit=3&offset=3')).length, 2);
  assert.equal((await err(api('GET', '/api/history?limit=abc'))).status, 400);
  assert.equal((await err(api('GET', '/api/history?offset='))).status, 400);
  assert.equal((await api('GET', '/api/history?limit=0')).length, 1);
  const q = await api('GET', `/api/questions/${s.questions[0].id}`);
  assert.equal(q.answer, 'ア'); assert.equal(q.attempts.length, 1); assert.equal(q.pool, 'study');
  assert.equal((await err(api('GET', '/api/questions/none'))).status, 404);
});

test('画像 URL は mediaUrl 経由で入る（A は image_url と image_urls）', async () => {
  const { api, start } = await makeEnv();
  const s = await start({ mode: 'study', size: 15, category_ids: [1] });
  assert.ok(s.questions.length);
  for (const q of s.questions) assert.equal(q.image_url, null);
  const d = await api('GET', '/api/questions/fe-30_aki-q011');
  assert.equal(d.image_url, null);
});

/* ------------- test_selection.py ------------- */
test('学習に mock は絶対に出ない', async () => {
  for (let seed = 0; seed < 20; seed++) {
    const { start } = await makeEnv({ rng: seeded(seed) });
    const s = await start({ mode: 'study', size: 15 });
    assert.equal(new Set(s.questions.map((q) => q.id)).size, 15);
    assert.ok(s.questions.every((q) => !q.id.startsWith('fe-05_haru') && q.part === 'A'));
  }
  const { api, start } = await makeEnv();
  const all = await start({ mode: 'study', size: 100 });
  assert.equal(all.questions.length, 69);
  assert.ok(all.questions.every((q) => !q.id.startsWith('fe-05_haru') && !q.id.startsWith('feb-')));
  assert.equal((await api('GET', '/api/home')).pool.study, 69);
});

test('include_calc=false なら計算問題は出ない', async () => {
  const { api, start } = await makeEnv();
  await api('PUT', '/api/settings', { include_calc: false });
  for (let i = 0; i < 10; i++) {
    const s = await start({ mode: 'study', size: 15 });
    assert.ok(s.questions.every((q) => !q.is_calc));
  }
  assert.equal((await start({ mode: 'study', size: 100 })).questions.length, 66);
});

test('分野絞り込みと同一 rng で決定的', async () => {
  const { start } = await makeEnv();
  const s = await start({ mode: 'study', size: 15, category_ids: [4, 7] });
  assert.equal(s.questions.length, 6);
  assert.deepEqual(new Set(s.questions.map((q) => q.category_id)), new Set([4, 7]));
  const a = await (await makeEnv({ rng: seeded(7) })).start({ mode: 'study' });
  const b = await (await makeEnv({ rng: seeded(7) })).start({ mode: 'study' });
  assert.deepEqual(a.questions.map((q) => q.id), b.questions.map((q) => q.id));
});

test('優先度: 不正解・遅い正解・速い正解の枠', async () => {
  const env = await makeEnv({ rng: seeded(3) });
  const qs = (await env.store.getAll('questions')).filter((q) => q.pool === 'study' && q.part === 'A').map((q) => q.id).sort().slice(0, 6);
  await addAttempt(env.store, qs[0], 'イ', 0, 1000, '2020-01-01 10:00:00');
  await addAttempt(env.store, qs[1], 'ア', 1, 200000, '2020-01-01 10:00:00');
  await addAttempt(env.store, qs[2], 'ア', 1, 1000, '2020-01-01 10:00:00');
  const s = await env.start({ mode: 'study' });
  const ids = s.questions.map((q) => q.id);
  assert.ok(ids.includes(qs[0]) && ids.includes(qs[1]) && ids.includes(qs[2]));
  assert.equal((await env.start({ mode: 'study', size: 69 })).questions.length, 69);
});

test('同日の不正解は後回し', async () => {
  for (let seed = 0; seed < 10; seed++) {
    const env = await makeEnv({ rng: seeded(seed) });
    const qs = (await env.store.getAll('questions')).filter((q) => q.pool === 'study' && q.part === 'A').map((q) => q.id).sort().slice(0, 4);
    await addAttempt(env.store, qs[0], 'イ', 0, 1000, '2020-01-01 10:00:00');
    await addAttempt(env.store, qs[1], 'イ', 0, 1000, NOW);
    let ids = (await env.start({ mode: 'study', size: 5 })).questions.map((q) => q.id);
    assert.ok(ids.includes(qs[0]) && ids.includes(qs[1]));
    ids = (await env.start({ mode: 'study', size: 3 })).questions.map((q) => q.id);
    assert.ok(ids.includes(qs[0]));
  }
});

test('枠配分: wrong 30% / unseen 50% / slow 10% / mastered 残り', async () => {
  // size 10: wrong3 unseen5 slow1 mastered1。十分な数の各状態を用意して配分を確認
  const env = await makeEnv({ rng: seeded(11) });
  const qs = (await env.store.getAll('questions')).filter((q) => q.pool === 'study' && q.part === 'A').map((q) => q.id).sort();
  const state = {};
  qs.slice(0, 10).forEach((q) => { state[q] = 'wrong'; });
  qs.slice(10, 20).forEach((q) => { state[q] = 'slow'; });
  qs.slice(20, 30).forEach((q) => { state[q] = 'mastered'; });
  for (const q of qs.slice(0, 10)) await addAttempt(env.store, q, 'イ', 0, 1000, '2020-01-01 10:00:00');
  for (const q of qs.slice(10, 20)) await addAttempt(env.store, q, 'ア', 1, 200000, '2020-01-01 10:00:00');
  for (const q of qs.slice(20, 30)) await addAttempt(env.store, q, 'ア', 1, 1000, '2020-01-01 10:00:00');
  for (let i = 0; i < 5; i++) {
    const s = await env.start({ mode: 'study', size: 10 });
    const cnt = { wrong: 0, slow: 0, mastered: 0, unseen: 0 };
    for (const q of s.questions) cnt[state[q.id] || 'unseen']++;
    assert.deepEqual(cnt, { wrong: 3, unseen: 5, slow: 1, mastered: 1 });
  }
});

test('pick_mock', async () => {
  const { start, err } = await makeEnv();
  const s = await start({ mode: 'mock', exam_code: '05_haru' });
  assert.deepEqual(s.questions.map((q) => q.id), Array.from({ length: 10 }, (_, i) => `fe-05_haru-q${String(i + 1).padStart(2, '0')}`));
  assert.equal((await start({ mode: 'mock', size: 4 })).questions.length, 4);
  assert.equal((await err(start({ mode: 'mock', exam_code: '99_x' }))).status, 409);
  assert.equal((await err(start({ mode: 'mock', exam_code: 5 }))).status, 400);
  assert.equal((await err(start({ mode: 'bad' }))).status, 400);
  assert.equal((await err(start({ mode: 'study', size: 0 }))).status, 400);
  assert.equal((await err(start({ mode: 'study', size: 101 }))).status, 400);
  assert.equal((await err(start({ mode: 'study', part: 'C' }))).status, 400);
  assert.equal((await err(start({ mode: 'study', part: null }))).status, 400);
});

/* ------------- test_stats.py ------------- */
test('分野別成績', async () => {
  const { api, store } = await makeEnv();
  await addAttempt(store, 'fe-30_aki-q011', 'イ', 0, 40000, '2020-01-01 10:00:00');
  await addAttempt(store, 'fe-30_aki-q011', 'ア', 1, 20000, '2020-01-02 10:00:00');
  await addAttempt(store, 'fe-30_aki-q012', 'ア', 1, 100000, '2020-01-02 10:00:00');
  await addAttempt(store, 'fe-30_aki-q021', 'イ', 0, 60000, '2020-01-02 10:00:00');
  const d = await api('GET', '/api/stats/categories');
  assert.deepEqual(d.fields.map((f) => f.field), ['te', 'ma', 'st']);
  assert.equal(d.fields.reduce((t, f) => t + f.categories.length, 0), 23);
  const c1 = d.fields[0].categories[0];
  assert.deepEqual([c1.total, c1.seen, c1.attempts, c1.correct], [3, 2, 3, 2]);
  assert.equal(c1.accuracy, 1.0); assert.equal(c1.mastered, 1);
  assert.equal(c1.avg_time_ms, Math.floor((40000 + 20000 + 100000) / 3));
  const c2 = d.fields[0].categories[1];
  assert.equal(c2.accuracy, 0.0); assert.equal(c2.seen, 1);
  assert.equal(d.fields[0].categories[2].accuracy, null);
  const h = await api('GET', '/api/home');
  assert.deepEqual(h.weak_categories[0], { id: 2, name: 'アルゴリズムとプログラミング', accuracy: 0.0 });
  assert.ok(h.weak_categories.length <= 3);
});

test('時間分析', async () => {
  const { api, store } = await makeEnv();
  await addAttempt(store, 'fe-30_aki-q011', 'ア', 1, 20000, '2026-10-17 10:00:00'); // 40 日前
  await addAttempt(store, 'fe-30_aki-q021', 'イ', 0, 100000, '2020-01-02 10:00:00');
  await addAttempt(store, 'fe-30_aki-q023', 'ア', 1, 140000, '2020-01-02 10:00:00');
  await addAttempt(store, 'fe-30_aki-q012', 'ア', 1, 50000, NOW);
  const d = await api('GET', '/api/stats/time');
  assert.deepEqual(d.overall, { avg_ms: 77500, median_ms: 75000, over_target_rate: 0.5 });
  assert.deepEqual(d.calc_vs_noncalc.calc, { avg_ms: 140000, accuracy: 1.0, n: 1 });
  assert.equal(d.calc_vs_noncalc.noncalc.n, 3);
  assert.deepEqual(d.slowest.map((x) => x.time_ms), [140000, 100000, 50000, 20000]);
  assert.equal(d.slowest[0].label, '平成30年秋期 問23');
  const sp = Object.fromEntries(d.speed_accuracy.map((b) => [b.bucket, [b.n, b.accuracy]]));
  assert.deepEqual(sp, { '〜30秒': [1, 1.0], '30〜60秒': [1, 1.0], '60〜90秒': [0, null], '90〜120秒': [1, 0.0], '120秒〜': [1, 1.0] });
  assert.equal(d.daily.length, 1); assert.equal(d.daily[0].n, 1); assert.equal(d.daily[0].accuracy, 1.0);
  assert.equal(d.daily[0].date, '2026-11-26');
  const cats = Object.fromEntries(d.by_category.map((c) => [c.id, c]));
  assert.equal(cats[2].n, 2); assert.equal(cats[2].avg_ms, 120000); assert.equal(cats[2].median_ms, 120000);
});

test('streak と today（now を注入）', async () => {
  const { api, store, clock } = await makeEnv();
  await addAttempt(store, 'fe-30_aki-q011', 'ア', 1, 1000, '2020-01-02 10:00:00');
  // 昨日から 3 日連続 + 飛んで 5 日前
  for (const [off, day] of [[1, '2026-11-25'], [2, '2026-11-24'], [3, '2026-11-23'], [5, '2026-11-21']]) {
    await addAttempt(store, `fe-30_aki-q02${(off % 3) + 1}`, 'ア', 1, 1000, `${day} 12:00:00`);
  }
  assert.equal((await api('GET', '/api/home')).streak_days, 3);
  const s = await (async () => { const e = await api('POST', '/api/sessions', { mode: 'study', size: 5 }); return e; })();
  await api('POST', `/api/sessions/${s.id}/answer`, { question_id: s.questions[0].id, chosen: 'ア', time_ms: 1 });
  assert.equal((await api('GET', '/api/home')).streak_days, 4);
  assert.equal((await api('GET', '/api/home')).today.answered, 1);
  // 日付が進むと今日は 0 件でも昨日までの連続が残る
  clock.set('2026-11-27 08:00:00');
  const h = await api('GET', '/api/home');
  assert.equal(h.streak_days, 4); assert.equal(h.today.answered, 0);
  clock.set('2026-11-28 08:00:00');
  assert.equal((await api('GET', '/api/home')).streak_days, 0);
});

test('空の成績', async () => {
  const { api } = await makeEnv();
  const d = await api('GET', '/api/stats/time');
  assert.equal(d.overall.avg_ms, null); assert.deepEqual(d.slowest, []); assert.equal(d.speed_accuracy.length, 5);
  assert.equal(d.overall.over_target_rate, null);
});

test('mock 回答は unfinished の間 history に出ないが today には入る（Python と同じ）', async () => {
  const { api, start } = await makeEnv();
  const s = await start({ mode: 'mock', exam_code: '05_haru' });
  await api('POST', `/api/sessions/${s.id}/answer`, { question_id: s.questions[0].id, chosen: 'ア', time_ms: 1000 });
  assert.deepEqual(await api('GET', '/api/history'), []);
  assert.equal((await api('GET', '/api/home')).today.answered, 1);
  assert.equal((await api('GET', '/api/home')).pool.study_unseen, 69);
});

test('レスポンスは複製（呼び出し側が壊してもキャッシュは無事）', async () => {
  const { api, start } = await makeEnv();
  const s = await start({ mode: 'study', size: 5 });
  s.questions[0].choices[0].text = 'こわした';
  const s2 = await api('GET', `/api/sessions/${s.id}`);
  assert.notEqual(s2.questions[0].choices[0].text, 'こわした');
});
