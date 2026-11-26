'use strict';
// `node --test tests/js/` 用の入口（Node 22 はディレクトリ引数を index.js として読むため）。
// 個別実行: node --test tests/js/*.test.js
const fs = require('fs');
const path = require('path');
for (const f of fs.readdirSync(__dirname).sort()) {
  if (f.endsWith('.test.js')) require(path.join(__dirname, f));
}
