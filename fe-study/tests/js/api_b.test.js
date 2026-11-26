'use strict';
// tests/test_api_b.py の JS 移植
const test = require('node:test');
const assert = require('node:assert/strict');
const { makeEnv, seeded } = require('./helpers.js');

const gids = (s) => s.questions.map((q) => q.group_id || q.id);
const sessB = (env, o = {}) => env.start(Object.assign({ mode: 'study', part: 'B' }, o));

test('B 設定の既定値と検証', async () => {
  const { api } = await makeEnv();
  const s = await api('GET', '/api/settings');
  assert.equal(s.session_size_b, 2); assert.equal(s.target_sec_b, 300); assert.deepEqual(s.b_topics, []);
  const r = await api('PUT', '/api/settings', { session_size_b: 4, b_topics: ['security'], target_sec_b: 240 });
  assert.deepEqual(r.b_topics, ['security']); assert.equal(r.session_size_b, 4);
});

test('B 出題: size はグループ数・同一グループは連続・sub_label 順', async () => {
  for (const size of [1, 2, 3, 6, 10]) {
    for (let seed = 0; seed < 8; seed++) {
      const env = await makeEnv({ rng: seeded(seed) });
      const s = await sessB(env, { size });
      const g = gids(s);
      assert.equal(new Set(g).size, Math.min(size, 6));
      const seen = new Set(); let prev = null;
      for (const x of g) {
        if (x !== prev) { assert.ok(!seen.has(x), '同一グループが分断'); seen.add(x); }
        prev = x;
      }
      assert.equal(s.part, 'B'); assert.equal(s.mode, 'study');
      assert.ok(s.questions.every((q) => q.part === 'B'));
      if (g.includes('furoku3-q01')) {
        assert.deepEqual(s.questions.filter((q) => q.group_id === 'furoku3-q01').map((q) => q.sub_label), ['設問1', '設問2', '設問3(2)']);
      }
    }
  }
});

test('B の既定サイズは設定から', async () => {
  const env = await makeEnv();
  assert.equal(new Set(gids(await sessB(env))).size, 2);
  await env.api('PUT', '/api/settings', { session_size_b: 4 });
  assert.equal(new Set(gids(await sessB(env))).size, 4);
});

test('B のオブジェクト項目', async () => {
  const env = await makeEnv();
  const s = await sessB(env, { size: 6 });
  const q = s.questions.find((x) => x.id === 'feb-sample-q01');
  assert.deepEqual(q.image_urls, ['/media/b/feb-sample-q01-1.png', '/media/b/feb-sample-q01-2.png']);
  assert.equal(q.b_topic, 'pseudo'); assert.deepEqual(q.tags, ['トレース', '再帰']); assert.equal(q.difficulty, 'やさしい');
  assert.equal(q.has_book_explanation, true); assert.equal(q.sub_label, null); assert.equal(q.group_id, null);
  assert.equal(q.choices.length, 10); assert.equal(q.choices.at(-1).label, 'コ');
  assert.equal(s.questions.find((x) => x.id === 'feb-sample-q03').has_book_explanation, false);
  assert.equal(q.explanation_url, '');
  assert.equal(q.image_url, null);
});

test('B は mock を出さず、A に B は混ざらない', async () => {
  const env = await makeEnv({ rng: seeded(5) });
  for (let i = 0; i < 5; i++) {
    const s = await sessB(env, { size: 10 });
    assert.ok(s.questions.every((q) => !q.id.startsWith('feb-05')));
    const a = await env.start({ mode: 'study', size: 15 });
    assert.ok(a.questions.every((q) => q.part === 'A' && !q.id.startsWith('feb-')));
  }
  let m = await env.start({ mode: 'mock', exam_code: '05_haru' });
  assert.equal(m.questions.length, 10); assert.ok(m.questions.every((q) => q.part === 'A'));
  m = await env.start({ mode: 'mock' });
  assert.ok(m.questions.every((q) => q.part === 'A'));
});

test('b_topics 絞り込み', async () => {
  const env = await makeEnv();
  let s = await sessB(env, { size: 10, b_topics: ['pseudo'] });
  assert.deepEqual(new Set(s.questions.map((q) => q.b_topic)), new Set(['pseudo'])); assert.equal(s.questions.length, 3);
  await env.api('PUT', '/api/settings', { b_topics: ['security'] });
  s = await sessB(env, { size: 10 });
  assert.deepEqual(new Set(s.questions.map((q) => q.b_topic)), new Set(['security']));
  assert.equal(s.questions.length, 5); assert.equal(new Set(gids(s)).size, 3);
  s = await sessB(env, { size: 10, b_topics: ['pseudo', 'security'] });
  assert.equal(s.questions.length, 8);
  assert.equal((await env.err(env.start({ mode: 'study', part: 'B', b_topics: ['zzz'] }))).status, 400);
});

test('B 回答で解説画像が開示される', async () => {
  const env = await makeEnv();
  const { api } = env;
  const s = await sessB(env, { size: 6 });
  const q = s.questions.find((x) => x.id === 'feb-sample-q02'); // 正解 コ
  const r = await api('POST', `/api/sessions/${s.id}/answer`, { question_id: q.id, chosen: 'コ', time_ms: 100000 });
  assert.equal(r.is_correct, true); assert.equal(r.answer, 'コ');
  assert.deepEqual(r.explanation_image_urls, ['/media/bx/feb-sample-q02-e1.png']);
  assert.equal(r.explanation_source, '【テスト】解説出典');
  const cur = await api('GET', `/api/sessions/${s.id}`);
  const att = cur.questions.find((x) => x.id === q.id).attempt;
  assert.deepEqual(att.explanation_image_urls, ['/media/bx/feb-sample-q02-e1.png']); assert.equal(att.answer, 'コ');
  const other = cur.questions.find((x) => x.id !== q.id);
  assert.equal(other.attempt, null);
  assert.equal((await env.err(api('POST', `/api/sessions/${s.id}/answer`, { question_id: other.id, chosen: 'ン', time_ms: 1 }))).status, 400);
  const a = await env.start({ mode: 'study', size: 5 });
  assert.equal((await env.err(api('POST', `/api/sessions/${a.id}/answer`, { question_id: a.questions[0].id, chosen: 'オ', time_ms: 1 }))).status, 400);
});

test('B 模試', async () => {
  const env = await makeEnv();
  const { api } = env;
  const s = await env.start({ mode: 'mock', part: 'B', exam_code: '05_haru' });
  assert.deepEqual(s.questions.map((q) => q.id), [1, 2, 3].map((n) => `feb-05_haru-q0${n}`));
  assert.equal(s.time_limit_sec, 900); assert.equal(s.part, 'B');
  const q = s.questions[0];
  assert.deepEqual(await api('POST', `/api/sessions/${s.id}/answer`, { question_id: q.id, chosen: 'ア', time_ms: 3000 }), { saved: true });
  const cur = await api('GET', `/api/sessions/${s.id}`);
  assert.deepEqual(cur.questions[0].attempt, { chosen: 'ア', time_ms: 3000 });
  const d0 = await api('GET', `/api/questions/${q.id}`);
  assert.equal(d0.answer, null); assert.ok(!('explanation_image_urls' in d0));
  const f = await api('POST', `/api/sessions/${s.id}/finish`);
  assert.equal(f.total, 3); assert.equal(f.correct, 1); assert.equal(f.passed, false);
  const a0 = f.questions[0].attempt;
  assert.equal(a0.answer, 'ア'); assert.deepEqual(a0.explanation_image_urls, ['/media/bx/feb-05_haru-q01-e1.png']);
  assert.equal(f.questions[1].attempt, null);
  const d = await api('GET', `/api/questions/${q.id}`);
  assert.equal(d.answer, 'ア'); assert.ok(d.explanation_image_urls.length); assert.equal(d.part, 'B');
});

test('B 模試（全部）と復習 B', async () => {
  const env = await makeEnv();
  const { api, err } = env;
  const s = await env.start({ mode: 'mock', part: 'B' });
  assert.equal(s.questions.length, 3); assert.equal(s.time_limit_sec, 900);
  assert.equal((await err(env.start({ mode: 'mock', part: 'B', exam_code: 'zz' }))).status, 409);
  assert.equal((await err(env.start({ mode: 'review', part: 'B' }))).status, 409);
  const st = await sessB(env, { size: 6 });
  for (const q of st.questions) {
    let ch = ['feb-sample-q01', 'feb-furoku3-q01-2'].includes(q.id) ? 'イ' : 'ア';
    if (q.id === 'feb-sample-q02') ch = 'コ';
    await api('POST', `/api/sessions/${st.id}/answer`, { question_id: q.id, chosen: ch, time_ms: 1000 });
  }
  await api('POST', `/api/sessions/${st.id}/finish`);
  const h = await api('GET', '/api/home');
  assert.equal(h.pool_b.review, 2); assert.equal(h.pool.review, 0);
  const r = await env.start({ mode: 'review', part: 'B' });
  assert.deepEqual(new Set(r.questions.map((q) => q.id)), new Set(['feb-sample-q01', 'feb-furoku3-q01-2']));
  assert.equal((await err(env.start({ mode: 'review' }))).status, 409);
});

test('B 模試（回指定なし）は 12 問以内に収まり、グループは分断されない', async () => {
  const env = await makeEnv({ rng: seeded(2) });
  const s = await env.start({ mode: 'mock', part: 'B', size: 2 });
  assert.ok(s.questions.length <= 2);
});

test('home の B 項目', async () => {
  const env = await makeEnv();
  const h = await env.api('GET', '/api/home');
  assert.deepEqual(h.pool_b, { study: 8, study_unseen: 8, mock: 3, review: 0 });
  assert.deepEqual(h.pool, { study: 69, study_unseen: 69, mock: 10, review: 0 });
  assert.deepEqual(h.mock_exams.map((e) => [e.part, e.exam_code, e.count]), [['A', '05_haru', 10], ['B', '05_haru', 3]]);
  const s = await sessB(env);
  const a = (await env.api('GET', '/api/home')).active_session;
  assert.equal(a.part, 'B'); assert.equal(a.total, s.questions.length);
});

test('成績: part=B のカテゴリ・B 統計・時間 part', async () => {
  const env = await makeEnv();
  const { api, err } = env;
  const s = await sessB(env, { size: 6 });
  await env.answerAll(s, 'ア', 200000);
  const a = await api('GET', '/api/stats/categories');
  assert.equal(a.fields.reduce((t, f) => t + f.categories.reduce((u, c) => u + c.seen, 0), 0), 0);
  const b = await api('GET', '/api/stats/categories?part=B');
  const cats = Object.fromEntries(b.fields.flatMap((f) => f.categories).map((c) => [c.id, c]));
  assert.equal(cats[2].total, 3); assert.equal(cats[11].total, 5); assert.equal(cats[2].seen, 3);
  assert.equal((await err(api('GET', '/api/stats/categories?part=Z'))).status, 400);
  const d = await api('GET', '/api/stats/b');
  const topics = Object.fromEntries(d.topics.map((t) => [t.b_topic, t]));
  assert.equal(topics.pseudo.total, 3); assert.equal(topics.pseudo.seen, 3);
  assert.equal(topics.pseudo.accuracy, Math.round((2 / 3) * 1e4) / 1e4);
  assert.equal(topics.security.accuracy, 1.0); assert.equal(topics.security.avg_time_ms, 200000);
  assert.equal(topics.pseudo.name, '擬似言語');
  const tags = Object.fromEntries(d.tags.map((t) => [t.tag, t]));
  assert.equal(tags['トレース'].total, 2); assert.equal(tags['トレース'].accuracy, 0.5);
  assert.equal(tags['認証'].total, 4); assert.equal(tags['暗号'].total, 2); assert.equal(tags['再帰'].total, 1);
  assert.deepEqual(d.difficulty.map((x) => x.difficulty), ['やさしい', 'ふつう', '難しい']);
  const diff = Object.fromEntries(d.difficulty.map((x) => [x.difficulty, x]));
  assert.equal(diff['難しい'].total, 4); assert.equal(diff['やさしい'].accuracy, 1.0);
  // タグは件数降順、同数は名前順
  assert.deepEqual(d.tags.map((t) => t.tag).slice(0, 3), ['認証', 'トレース', '暗号']);
});

test('B 統計（空）', async () => {
  const { api } = await makeEnv();
  const d = await api('GET', '/api/stats/b');
  assert.deepEqual(d.topics.map((t) => t.accuracy), [null, null]);
  assert.ok(d.tags.every((t) => t.seen === 0));
});

test('時間分析の part 絞り込み', async () => {
  const env = await makeEnv();
  const { api, err } = env;
  await env.answerAll(await sessB(env, { size: 2 }), 'ア', 250000);
  await env.answerAll(await env.start({ mode: 'study', size: 5 }), 'ア', 10000);
  const n = async (p) => (await api('GET', `/api/stats/time?part=${p}`)).by_category.reduce((t, x) => t + x.n, 0);
  const nall = (await api('GET', '/api/stats/time')).by_category.reduce((t, x) => t + x.n, 0);
  assert.equal(await n('A'), 5); assert.ok((await n('B')) >= 2); assert.equal(nall, (await n('A')) + (await n('B')));
  assert.equal(nall, await n('all'));
  const t = await api('GET', '/api/stats/time?part=B');
  assert.equal(t.overall.avg_ms, 250000); assert.equal(t.overall.over_target_rate, 0.0);
  assert.equal((await err(api('GET', '/api/stats/time?part=X'))).status, 400);
});

test('履歴と単問の B 項目', async () => {
  const env = await makeEnv();
  const { api } = env;
  const s = await sessB(env, { size: 1 });
  await env.answerAll(s);
  const h = await api('GET', '/api/history');
  assert.equal(h.length, s.questions.length); assert.ok(h.every((x) => x.part === 'B'));
  const q0 = s.questions[0];
  const d = await api('GET', `/api/questions/${q0.id}`);
  assert.equal(d.part, 'B'); assert.ok(d.image_urls.length); assert.equal(d.answer, 'ア');
  assert.deepEqual(d.tags, q0.tags); assert.ok('has_book_explanation' in d);
  assert.deepEqual(d.explanation_image_urls, q0.has_book_explanation ? [`/media/bx/${q0.id}-e1.png`] : []);
  const a = await env.start({ mode: 'study', size: 5 });
  await env.answerAll(a);
  assert.equal((await api('GET', '/api/history?limit=1'))[0].part, 'A');
});

test('B の優先: 同日誤答でも size=6 なら全グループが入る', async () => {
  const env = await makeEnv();
  const st = await sessB(env, { size: 6 });
  await env.api('POST', `/api/sessions/${st.id}/answer`, { question_id: st.questions[0].id, chosen: 'イ', time_ms: 1 });
  assert.equal(new Set(gids(await sessB(env, { size: 6 }))).size, 6);
  assert.equal(new Set(gids(await sessB(env, { size: 2 }))).size, 2);
});
