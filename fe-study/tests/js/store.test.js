'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { FEStore, FELocal, fixtureQuestions, seeded } = require('./helpers.js');
const { createFakeIndexedDB } = require('./fake_idb.js');

async function stores() {
  globalThis.indexedDB = createFakeIndexedDB();
  return [['memory', FEStore.memory()], ['indexeddb(fake)', await FEStore.open()]];
}

test('スキーマは SPEC 7.3 / pack.js と一致', () => {
  const s = Object.fromEntries(FEStore.SCHEMA.map(([n, o, i]) => [n, [o, i.map((x) => x.join(':'))]]));
  assert.deepEqual(Object.keys(s), ['questions', 'blobs', 'sessions', 'attempts', 'settings', 'meta']);
  assert.deepEqual(s.questions, [{ keyPath: 'id' }, ['part:part', 'pool:pool']]);
  assert.deepEqual(s.blobs, [{ keyPath: 'path' }, []]);
  assert.deepEqual(s.sessions, [{ keyPath: 'id', autoIncrement: true }, ['status:status']]);
  assert.deepEqual(s.attempts, [{ keyPath: 'id', autoIncrement: true }, ['question_id:question_id', 'session_id:session_id']]);
  assert.deepEqual(s.settings, [{ keyPath: 'key' }, []]);
  assert.deepEqual(s.meta, [{ keyPath: 'key' }, []]);
  assert.equal(FEStore.DB_NAME, 'fe-study'); assert.equal(FEStore.DB_VERSION, 1);
});

test('put/get/getAll/index/autoIncrement/count/delete/clear（両実装で同じ挙動）', async () => {
  for (const [name, st] of await stores()) {
    const k1 = await st.put('sessions', { mode: 'study', status: 'active' });
    const k2 = await st.put('sessions', { mode: 'mock', status: 'done' });
    assert.deepEqual([k1, k2], [1, 2], name);
    assert.equal((await st.get('sessions', 2)).mode, 'mock', name);
    assert.equal((await st.get('sessions', 2)).id, 2, name);
    assert.deepEqual((await st.getAllByIndex('sessions', 'status', 'active')).map((s) => s.id), [1], name);
    await st.put('sessions', { id: 1, mode: 'study', status: 'done' });
    assert.equal(await st.count('sessions'), 2, name);
    assert.deepEqual((await st.getAllByIndex('sessions', 'status', 'done')).map((s) => s.id), [1, 2], name);
    assert.equal(await st.put('sessions', { mode: 'x', status: 'x' }), 3, name);
    await st.delete('sessions', 3);
    assert.equal(await st.get('sessions', 3), undefined, name);
    await st.putMany('questions', [{ id: 'b', part: 'A', pool: 'study' }, { id: 'a', part: 'B', pool: 'mock' }]);
    assert.deepEqual((await st.getAll('questions')).map((q) => q.id), ['a', 'b'], name); // キー順
    await st.clear(['questions', 'sessions']);
    assert.equal(await st.count('questions'), 0, name);
    assert.equal(await st.count('sessions'), 0, name);
  }
});

test('tx 内で throw すると全て巻き戻る（原子性）', async () => {
  for (const [name, st] of await stores()) {
    await st.put('sessions', { mode: 'study', status: 'active', current_index: 0 });
    await assert.rejects(st.tx(['sessions', 'attempts'], 'readwrite', async (t) => {
      const s = await t.get('sessions', 1);
      s.current_index = 5;
      await t.put('sessions', s);
      await t.put('attempts', { session_id: 1, question_id: 'q', chosen: 'ア', is_correct: 1, time_ms: 1, answered_at: 'x' });
      throw new Error('boom');
    }), /boom/, name);
    assert.equal((await st.get('sessions', 1)).current_index, 0, name);
    assert.equal(await st.count('attempts'), 0, name);
    // 巻き戻しのあと採番も戻る
    assert.equal(await st.put('attempts', { session_id: 1, question_id: 'q' }), 1, name);
  }
});

test('保存値は複製（呼び出し側の変更が混ざらない）', async () => {
  for (const [name, st] of await stores()) {
    const o = { id: 'q', part: 'A', pool: 'study', choices: [{ label: 'ア' }] };
    await st.put('questions', o);
    o.choices[0].label = 'X';
    const g = await st.get('questions', 'q');
    assert.equal(g.choices[0].label, 'ア', name);
    g.choices[0].label = 'Y';
    assert.equal((await st.get('questions', 'q')).choices[0].label, 'ア', name);
  }
});

test('IndexedDB 実装で API 一式が動く（偽 IDB）', async () => {
  globalThis.indexedDB = createFakeIndexedDB();
  const store = await FEStore.open();
  await store.putMany('questions', fixtureQuestions());
  FELocal.init({ store, rng: seeded(9), now: () => '2026-11-26 10:00:00', mediaUrl: (p) => `/m/${p}` });
  const api = (m, p, b) => FELocal.api(m, p, b);
  const s = await api('POST', '/api/sessions', { mode: 'study', size: 5 });
  assert.equal(s.questions.length, 5);
  const r = await api('POST', `/api/sessions/${s.id}/answer`, { question_id: s.questions[0].id, chosen: 'ア', time_ms: 3000 });
  assert.equal(r.is_correct, true);
  await assert.rejects(api('POST', `/api/sessions/${s.id}/answer`, { question_id: s.questions[1].id, chosen: 'Z', time_ms: 1 }), { status: 400 });
  const cur = await api('GET', '/api/sessions/active');
  assert.equal(cur.current_index, 1);
  const f = await api('POST', `/api/sessions/${s.id}/finish`);
  assert.equal(f.total, 5); assert.equal(f.correct, 1);
  assert.equal((await api('GET', '/api/home')).today.answered, 1);
  const b = await api('POST', '/api/sessions', { mode: 'study', part: 'B', size: 6 });
  assert.equal(b.questions.length, 8);
  await assert.rejects(api('GET', '/api/sessions/999'), { status: 404 });
  // 別接続（pack.js のように直接開く）から見える
  const st2 = await FEStore.open();
  assert.equal((await st2.getAll('attempts')).length, 5);
});

test('パック導入後は invalidate / メタ変更で問題一覧が更新される', async () => {
  const store = FEStore.memory();
  FELocal.init({ store, rng: seeded(1), now: () => '2026-11-26 10:00:00', mediaUrl: (p) => p });
  assert.equal((await FELocal.api('GET', '/api/home')).pool.study, 0);
  await store.putMany('questions', fixtureQuestions());
  assert.equal((await FELocal.api('GET', '/api/home')).pool.study, 69); // 件数が変わる
  const q = fixtureQuestions()[0]; q.body = '更新後';
  await store.put('questions', q);
  assert.notEqual((await FELocal.api('GET', '/api/questions/' + q.id)).body, '更新後'); // 件数同じ → キャッシュ
  FELocal.invalidate();
  assert.equal((await FELocal.api('GET', '/api/questions/' + q.id)).body, '更新後');
});

test('meta の categories があればそれを使う', async () => {
  const store = FEStore.memory();
  await store.putMany('questions', fixtureQuestions());
  const { L } = require('./helpers.js');
  const rows = L.CATEGORIES.map((c) => Object.assign({}, c, { name: c.id === 1 ? '改名した分野' : c.name }));
  await store.put('meta', { key: 'categories', rows, value: rows });
  FELocal.init({ store, rng: seeded(1), now: () => '2026-11-26 10:00:00', mediaUrl: (p) => p });
  const d = await FELocal.api('GET', '/api/stats/categories');
  assert.equal(d.fields[0].categories[0].name, '改名した分野');
  await store.clear(['meta']);
  FELocal.invalidate();
  await store.put('questions', fixtureQuestions()[1]); // sig を変えずに invalidate だけで戻ることも確認
  const d2 = await FELocal.api('GET', '/api/stats/categories');
  assert.equal(d2.fields[0].categories[0].name, '基礎理論');
});
