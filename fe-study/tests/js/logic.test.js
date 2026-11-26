'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { L, fixtureQuestions, seeded } = require('./helpers.js');

test('pyRound は Python の銀行丸め', () => {
  const cases = [[0.5, 0], [1.5, 2], [2.5, 2], [3.5, 4], [4.5, 4], [7.5, 8], [2.4, 2], [2.6, 3], [0, 0], [8 * 0.3, 2], [15 * 0.3, 4], [5 * 0.5, 2]];
  for (const [x, y] of cases) assert.equal(L.pyRound(x), y, String(x));
});

test('round4 は Python の round(x, 4) と一致（厳密な同点は偶数丸め）', () => {
  assert.equal(L.round4(2 / 3), 0.6667);
  assert.equal(L.round4(1 / 3), 0.3333);
  assert.equal(L.round4(1 / 32), 0.0312);   // 0.03125 -> 偶数側
  assert.equal(L.round4(3 / 32), 0.0938);   // 0.09375 -> 偶数側
  assert.equal(L.round4(5 / 32), 0.1562);   // 0.15625
  assert.equal(L.round4(1), 1);
  assert.equal(L.round4(0), 0);
  assert.equal(L.round4(0.5), 0.5);
});

test('median / nat', () => {
  assert.equal(L.median([5, 1, 3]), 3);
  assert.equal(L.median([1, 2, 3, 10]), 2.5);
  assert.deepEqual(L.nat('設問3(2)'), ['設問', 3, '(', 2, ')']);
  assert.deepEqual(L.nat(null), ['']);
  assert.deepEqual(L.nat('abc'), ['abc']);
});

test('shuffle は順列を保ち、rng に対して決定的', () => {
  const a = L.shuffle([1, 2, 3, 4, 5, 6], seeded(1)), b = L.shuffle([1, 2, 3, 4, 5, 6], seeded(1));
  assert.deepEqual(a, b);
  assert.deepEqual(a.slice().sort(), [1, 2, 3, 4, 5, 6]);
});

test('addDays / todayOf', () => {
  assert.equal(L.addDays('2026-03-01', -1), '2026-02-28');
  assert.equal(L.addDays('2024-03-01', -1), '2024-02-29');
  assert.equal(L.addDays('2026-12-31', 1), '2027-01-01');
  assert.equal(L.todayOf('2026-11-26 23:59:59'), '2026-11-26');
});

test('分類表は app/categories.py と同じ構成', () => {
  assert.equal(L.CATEGORIES.length, 23);
  assert.deepEqual(L.CATEGORIES.filter((c) => c.field === 'te').map((c) => c.id), Array.from({ length: 13 }, (_, i) => i + 1));
  assert.deepEqual(L.CATEGORIES.filter((c) => c.field === 'ma').map((c) => c.id), [14, 15, 16]);
  assert.equal(L.CATEGORIES[10].name, 'セキュリティ'); assert.equal(L.CATEGORIES[22].major_name, '企業と法務');
});

/* ---- 出題のプロパティテスト（ランダムな回答履歴の下で制約が常に成り立つ） ---- */
function randomAttempts(qs, rng, p) {
  const atts = [];
  let id = 0;
  const days = ['2026-11-26 09:00:00', '2026-11-20 09:00:00', '2026-10-01 09:00:00'];
  for (const q of qs) {
    if (q.pool !== 'study' || rng() > p) continue;
    const n = 1 + Math.floor(rng() * 3);
    for (let i = 0; i < n; i++) {
      atts.push({ id: ++id, session_id: 1, question_id: q.id, chosen: rng() < 0.1 ? null : 'ア',
        is_correct: rng() < 0.6 ? 1 : 0, time_ms: Math.floor(rng() * 200000), answered_at: days[Math.floor(rng() * 3)] });
    }
  }
  return atts;
}

test('pickStudy: 常に制約を満たす（pool・part・calc・分野・重複・件数）', () => {
  const qs = fixtureQuestions();
  const byId = new Map(qs.map((q) => [q.id, q]));
  for (let seed = 1; seed <= 200; seed++) {
    const rng = seeded(seed);
    const atts = randomAttempts(qs, rng, rng());
    const size = 1 + Math.floor(rng() * 15);
    const calc = rng() < 0.5;
    const cats = rng() < 0.5 ? [] : [1 + Math.floor(rng() * 23), 1 + Math.floor(rng() * 23)];
    const ids = L.pickStudy(qs, atts, size, calc, cats, rng, 90, '2026-11-26');
    const eligible = qs.filter((q) => q.pool === 'study' && q.part === 'A' && (calc || !q.is_calc) && (!cats.length || cats.includes(q.category_id)));
    assert.equal(ids.length, Math.min(size, eligible.length));
    assert.equal(new Set(ids).size, ids.length);
    for (const id of ids) {
      const q = byId.get(id);
      assert.equal(q.pool, 'study'); assert.equal(q.part, 'A');
      assert.ok(calc || !q.is_calc);
      assert.ok(!cats.length || cats.includes(q.category_id));
    }
  }
});

test('pickStudy: 枠配分（十分な数があるとき wrong30%/unseen50%/slow10%/mastered）', () => {
  const qs = fixtureQuestions().filter((q) => q.pool === 'study' && q.part === 'A');
  const atts = []; const state = {};
  qs.forEach((q, i) => {
    if (i < 15) { state[q.id] = 'wrong'; atts.push({ id: atts.length + 1, session_id: 1, question_id: q.id, chosen: 'イ', is_correct: 0, time_ms: 1000, answered_at: '2020-01-01 00:00:00' }); }
    else if (i < 30) { state[q.id] = 'slow'; atts.push({ id: atts.length + 1, session_id: 1, question_id: q.id, chosen: 'ア', is_correct: 1, time_ms: 999999, answered_at: '2020-01-01 00:00:00' }); }
    else if (i < 45) { state[q.id] = 'mastered'; atts.push({ id: atts.length + 1, session_id: 1, question_id: q.id, chosen: 'ア', is_correct: 1, time_ms: 1000, answered_at: '2020-01-01 00:00:00' }); }
  });
  const expected = { 5: [2, 2, 0, 1], 8: [2, 4, 1, 1], 10: [3, 5, 1, 1], 15: [4, 8, 2, 1] };
  for (const [size, [w, u, s, m]] of Object.entries(expected)) {
    for (let seed = 0; seed < 20; seed++) {
      const ids = L.pickStudy(qs, atts, Number(size), true, [], seeded(seed), 90, '2026-11-26');
      const c = { wrong: 0, unseen: 0, slow: 0, mastered: 0 };
      for (const id of ids) c[state[id] || 'unseen']++;
      assert.deepEqual([c.wrong, c.unseen, c.slow, c.mastered], [w, u, s, m], `size=${size} seed=${seed}`);
    }
  }
});

test('pickStudy: unseen は正答率の低い分野ほど選ばれやすい', () => {
  const qs = fixtureQuestions().filter((q) => q.pool === 'study' && q.part === 'A' && q.category_id <= 2);
  // 分野 1 は全問正解、分野 2 は全問不正解。ただし各分野に未回答を 1 問ずつ残す
  const atts = [];
  const seen = qs.filter((q) => !q.id.endsWith('3'));
  for (const q of seen) atts.push({ id: atts.length + 1, session_id: 1, question_id: q.id, chosen: 'x', is_correct: q.category_id === 1 ? 1 : 0, time_ms: 1000, answered_at: '2020-01-01 00:00:00' });
  const rng = seeded(42); let c1 = 0, c2 = 0;
  for (let i = 0; i < 2000; i++) {
    const ids = L.pickStudy(qs, atts, 1, true, [], rng, 90, '2026-11-26'); // size=1: wrong=0 unseen=1(0.5→0?)
    void ids;
  }
  for (let i = 0; i < 2000; i++) {
    const ids = L.pickStudy(qs, atts, 2, true, [], rng, 90, '2026-11-26'); // unseen 枠 1 + wrong 枠 1 → unseen は未回答 2 問から 1 問
    for (const id of ids) { if (id.endsWith('3')) { if (id.startsWith('fe-30_aki-q01')) c1++; else c2++; } }
  }
  assert.ok(c2 > c1 * 1.1, `c1=${c1} c2=${c2}`); // 重み 2.0 : 1.0 → 約 2:1 より緩い下限で確認
});

test('pickReview: 最新が不正解のみ・分野絞り込み・size', () => {
  const qs = fixtureQuestions();
  const mk = (id, q, ok, at) => ({ id, session_id: 1, question_id: q, chosen: 'ア', is_correct: ok, time_ms: 1, answered_at: at });
  const atts = [mk(1, 'fe-30_aki-q011', 0, 'a'), mk(2, 'fe-30_aki-q011', 1, 'b'), mk(3, 'fe-30_aki-q021', 0, 'c'),
    mk(4, 'fe-30_aki-q031', 0, 'd'), mk(5, 'feb-sample-q01', 0, 'e')];
  assert.deepEqual(L.reviewIds(qs, atts), ['fe-30_aki-q031', 'fe-30_aki-q021']);
  assert.deepEqual(L.reviewIds(qs, atts, [2]), ['fe-30_aki-q021']);
  assert.deepEqual(L.reviewIdsB(qs, atts), ['feb-sample-q01']);
  assert.equal(L.pickReview(qs, atts, 1, [], seeded(1)).length, 1);
  // chosen=null（未回答）は履歴に数えない
  assert.deepEqual(L.reviewIds(qs, [{ id: 1, session_id: 1, question_id: 'fe-30_aki-q011', chosen: null, is_correct: 0, time_ms: 0, answered_at: 'x' }]), []);
});

test('pickMock: 回指定は番号順に全問・無指定は size 問、B は mock のみ・グループ維持', () => {
  const qs = fixtureQuestions();
  assert.equal(L.pickMock(qs, '05_haru', 20, seeded(1)).length, 10);
  assert.equal(L.pickMock(qs, null, 4, seeded(1)).length, 4);
  assert.deepEqual(L.pickMock(qs, '99', 20, seeded(1)), []);
  assert.deepEqual(L.pickMockB(qs, '05_haru', 12, seeded(1)), ['feb-05_haru-q01', 'feb-05_haru-q02', 'feb-05_haru-q03']);
  // グループ付き mock: 3 設問のグループ + 単独 2 問、上限 4 → グループ全体が入らない場合は丸ごと除外
  const grp = ['x1', 'x2', 'x3'].map((s, i) => Object.assign({}, qs.find((q) => q.id === 'feb-05_haru-q01'), { id: 'g' + s, group_id: 'G', sub_label: `設問${3 - i}`, number: 1 }));
  const singles = ['feb-05_haru-q01', 'feb-05_haru-q02'].map((id) => qs.find((q) => q.id === id));
  for (let seed = 0; seed < 30; seed++) {
    const ids = L.pickMockB(grp.concat(singles), null, 4, seeded(seed));
    assert.ok(ids.length <= 4);
    const inG = ids.filter((i) => i.startsWith('g'));
    assert.ok(inG.length === 0 || inG.length === 3, 'グループは丸ごと');
    if (inG.length) assert.deepEqual(inG, ['gx3', 'gx2', 'gx1']); // 設問1,2,3 の順 = sub_label 昇順
  }
});

test('pickStudyB: ランダムな履歴でもグループ連続・size=グループ数・topic 絞り込み', () => {
  const qs = fixtureQuestions();
  for (let seed = 1; seed <= 150; seed++) {
    const rng = seeded(seed);
    const atts = randomAttempts(qs, rng, rng());
    const size = 1 + Math.floor(rng() * 8);
    const topics = [[], ['pseudo'], ['security']][Math.floor(rng() * 3)];
    const ids = L.pickStudyB(qs, atts, size, topics, rng, 300, '2026-11-26');
    const byId = new Map(qs.map((q) => [q.id, q]));
    const elig = qs.filter((q) => q.pool === 'study' && q.part === 'B' && (!topics.length || topics.includes(q.b_topic)));
    const units = new Set(elig.map((q) => q.group_id || q.id));
    const got = ids.map((i) => byId.get(i).group_id || i);
    assert.equal(new Set(got).size, Math.min(size, units.size));
    assert.equal(new Set(ids).size, ids.length);
    let prev = null; const seen = new Set();
    for (const g of got) { if (g !== prev) { assert.ok(!seen.has(g)); seen.add(g); } prev = g; }
    for (const id of ids) { const q = byId.get(id); assert.equal(q.pool, 'study'); assert.equal(q.part, 'B'); }
  }
});

test('planSession の検証メッセージ', () => {
  const qs = fixtureQuestions();
  const st = L.mergeSettings({});
  const plan = (d) => L.planSession(d, st, qs, [], seeded(1), '2026-11-26 10:00:00');
  assert.throws(() => plan({ mode: 'x' }), { status: 400, error: 'mode は study/review/mock です' });
  assert.throws(() => plan({ mode: 'study', part: 'C' }), { status: 400, error: 'part は A/B です' });
  assert.throws(() => plan({ mode: 'study', size: 0 }), { status: 400, error: 'size が不正です' });
  assert.throws(() => plan({ mode: 'review' }), { status: 409, error: '出題できる問題がありません' });
  assert.throws(() => plan({ mode: 'study', category_ids: [24] }), { status: 400 });
  assert.deepEqual(plan({ mode: 'study' }).opts, { size: 8, include_calc: true, category_ids: [] });
  assert.deepEqual(plan({ mode: 'study', part: 'B' }).opts, { part: 'B', size: 2, b_topics: [] });
  assert.equal(plan({ mode: 'mock', exam_code: '05_haru' }).limit, 900);
});

test('validateSettings: 数値の境界と整形', () => {
  assert.deepEqual(L.validateSettings({ session_size: 5, target_sec: 10 }), { session_size: 5, target_sec: 10 });
  assert.deepEqual(L.validateSettings({ session_size: 15, target_sec: 600, session_size_b: 4, target_sec_b: 1800 }),
    { session_size: 15, target_sec: 600, session_size_b: 4, target_sec_b: 1800 });
  for (const bad of [{ session_size: 4.5 }, { target_sec: 601 }, { target_sec_b: 29 }, { font_scale: '1.0' }, { theme: null }, { include_calc: 'true' }]) {
    assert.throws(() => L.validateSettings(bad), { status: 400 }, JSON.stringify(bad));
  }
  assert.deepEqual(L.validateSettings({ category_ids: [3, 1, 3] }), { category_ids: [1, 3] });
  assert.deepEqual(L.validateSettings({ b_topics: ['security', 'pseudo', 'pseudo'] }), { b_topics: ['pseudo', 'security'] });
  assert.throws(() => L.validateSettings({ foo: 1 }), { error: '未知の設定キー: foo' });
});

test('統計の純粋関数: 空入力・median の偶数個・daily の 30 日窓', () => {
  const qs = fixtureQuestions();
  const st = L.mergeSettings({});
  const t = L.timeStats(qs, [], st, 'all', '2026-11-26');
  assert.equal(t.overall.median_ms, null);
  const mk = (id, q, ms, at) => ({ id, session_id: 1, question_id: q, chosen: 'ア', is_correct: 1, time_ms: ms, answered_at: at });
  const atts = [mk(1, 'fe-30_aki-q011', 10001, '2026-10-28 10:00:00'), mk(2, 'fe-30_aki-q011', 20000, '2026-10-27 10:00:00'),
    mk(3, 'fe-30_aki-q012', 30000, '2026-11-26 10:00:00')];
  const r = L.timeStats(qs, atts, st, 'all', '2026-11-26');
  assert.deepEqual(r.daily.map((d) => d.date), ['2026-10-28', '2026-11-26']); // 29 日前は含み、30 日前は除外
  assert.equal(r.overall.median_ms, 20000);
  assert.equal(r.overall.avg_ms, Math.trunc((10001 + 20000 + 30000) / 3));
  const r2 = L.timeStats(qs, atts.slice(0, 2), st, 'all', '2026-11-26');
  assert.equal(r2.overall.median_ms, Math.trunc((10001 + 20000) / 2)); // int(15000.5) = 15000
});
