'use strict';
// パリティ検証の Node 側: Python が書き出したデータを読み込み、同じリクエスト列を FELocal.api で実行して結果を JSON で返す。
// 使い方: node parity_node.js data.json  （parity.py から呼ばれる）
const fs = require('fs');
const path = require('path');
const LOCAL = path.join(__dirname, '..', '..', 'static', 'local');
const FEStore = require(path.join(LOCAL, 'store.js'));
const FELocal = require(path.join(LOCAL, 'api-local.js'));

async function main() {
  const d = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
  const store = FEStore.memory();
  await store.putMany('questions', d.questions);
  await store.putMany('sessions', d.sessions);
  await store.putMany('attempts', d.attempts);
  await store.putMany('settings', Object.entries(d.settings).map(([key, value]) => ({ key, value })));
  let i = 0;
  const vals = d.rng_values;
  FELocal.init({ store, rng: () => vals[i++ % vals.length], now: () => d.now, mediaUrl: (p) => `/media/${p}` });
  const call = async (r) => {
    try { return await FELocal.api(r.method, r.path, r.body); } catch (e) {
      if (e && e.status != null) return { __error: true, status: e.status, error: e.error };
      return { __error: true, status: -1, error: String(e && e.stack || e) };
    }
  };
  const phase1 = [];
  for (const r of d.phase1) phase1.push(await call(r));
  const scenario = [];
  for (const r of d.scenario) scenario.push(await call(r));
  process.stdout.write(JSON.stringify({ phase1, scenario }));
}
main().catch((e) => { console.error(e); process.exit(1); });
