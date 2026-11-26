# FE Study — 設計仕様（全タスク共通の契約）

基本情報技術者試験（FE）科目A対策用の、スマホで使う個人学習アプリ。
Python (Flask) + SQLite。Tailscale 経由で自分の端末からのみアクセスする。

## 0. 前提・方針

- 出題範囲・分類は **IPA「基本情報技術者試験（レベル2）」シラバス Ver.9.2** に準拠する。
  - 中分類 23 個 = カテゴリ ID 1〜23（過去問道場のカテゴリ番号と一致）。
- 問題本文は **IPA 公式の公開問題 PDF** から取り込む（IPA は許諾・使用料不要と明記。出典表示必須）。
  - 出典表示例: `出典：令和元年度 秋期 基本情報技術者試験 午前 問1（IPA）`
- fe-siken.com（過去問道場）は **スクレイピングしない**（robots.txt で AI クローラー拒否、解説文はサイトの著作物）。
  各問題に「過去問道場で解説を見る」リンクのみ付与する:
  `https://www.fe-siken.com/kakomon/{exam_code}/q{number}.html`
- **令和5〜8年（exam_code: 05_*, 06_*, 07_*, 08_*）の問題は模擬試験専用**（`pool='mock'`）。学習モードでは絶対に出題しない。
  それ以外（〜令和4年）は `pool='study'`。
- 対象は科目A（旧・午前）のみ。科目B は将来拡張。
- 生成データ（DB・画像・PDF キャッシュ）は `fe-study/data/` に置き、git 管理しない。

## 1. ディレクトリ構成

```
fe-study/
  app/
    __init__.py        # create_app(db_path=None) -> Flask
    db.py              # 接続, init_db(), スキーマ適用, カテゴリ初期投入
    schema.sql
    categories.py      # 23 中分類の定義（シラバス9.2 大分類/中分類番号つき）
    api.py             # Blueprint /api
    selection.py       # 出題アルゴリズム
    stats.py           # 成績・時間分析
  importer/
    __main__.py        # python -m importer [--years ...] [--offline DIR]
    ipa_fetch.py       # IPA から PDF 取得（キャッシュ・レート制限）
    pdf_parse.py       # 問題/選択肢/図の抽出、解答例 PDF の解析
    classify.py        # シラバス9.2 キーワードによる中分類推定
    calc_detect.py     # 計算問題判定
    syllabus_keywords.json  # シラバス9.2 から作ったキーワード辞書
  static/
    index.html  app.css  app.js  manifest.webmanifest  icon.svg
  tests/
  scripts/ setup.sh  run.sh  fe-study.service
  docs/ SPEC.md  RESOURCES.md  DEPLOY_TAILSCALE.md  TASKS.md
  data/ (gitignore)  fe.db  images/  cache/
  requirements.txt  README.md  .gitignore
```

実行: `cd fe-study && python -m app`（既定 127.0.0.1:8000、`FE_HOST`/`FE_PORT`/`FE_DB` 環境変数で変更）。
本番は waitress。

## 2. SQLite スキーマ（app/schema.sql）

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,          -- 1..23 (シラバス9.2 中分類番号)
  field TEXT NOT NULL,             -- 'te' テクノロジ系 | 'ma' マネジメント系 | 'st' ストラテジ系
  major_no INTEGER NOT NULL,       -- シラバス 大分類番号 1..9
  major_name TEXT NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,             -- 例 'fe-01_aki-q01'  ('fe-' || exam_code || '-q' || 2桁番号)
  exam_code TEXT NOT NULL,         -- 過去問道場と同じ回コード '01_aki','31_haru','05_menjo','08_haru' 等
  exam_label TEXT NOT NULL,        -- '令和元年秋期'
  number INTEGER NOT NULL,         -- 問番号
  category_id INTEGER NOT NULL REFERENCES categories(id),
  body TEXT NOT NULL,              -- 問題文（プレーンテキスト、改行あり）
  choices TEXT NOT NULL,           -- JSON: [{"label":"ア","text":"..."}, ...]  text は空でもよい（図のみ等）
  answer TEXT NOT NULL,            -- 'ア'|'イ'|'ウ'|'エ'
  image_path TEXT,                 -- data/images/ 以下の相対パス。図・表・数式があり本文で表せない場合の問題全体の切り抜き PNG
  is_calc INTEGER NOT NULL DEFAULT 0,
  pool TEXT NOT NULL CHECK (pool IN ('study','mock')),
  source_label TEXT NOT NULL,      -- 出典表示文字列
  source_url TEXT NOT NULL,        -- IPA PDF URL
  explanation_url TEXT NOT NULL,   -- 過去問道場の解説 URL
  parse_confidence REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_q_pool_cat ON questions(pool, category_id);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL CHECK (mode IN ('study','review','mock')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','abandoned')),
  question_ids TEXT NOT NULL,      -- JSON 配列（出題順）
  current_index INTEGER NOT NULL DEFAULT 0,
  current_elapsed_ms INTEGER NOT NULL DEFAULT 0,  -- 回答中の問題の経過時間（オートセーブ用）
  time_limit_sec INTEGER,          -- 模試のみ
  options TEXT NOT NULL DEFAULT '{}',  -- JSON: 作成時の設定スナップショット
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  question_id TEXT NOT NULL REFERENCES questions(id),
  chosen TEXT,                     -- NULL = 未回答（模試の時間切れ等）
  is_correct INTEGER NOT NULL,
  time_ms INTEGER NOT NULL,
  answered_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE(session_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_att_q ON attempts(question_id);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL              -- JSON
);
```

設定キーと既定値:
| key | 既定 | 意味 |
|---|---|---|
| include_calc | true | 計算問題を出題に含める |
| session_size | 8 | 1 回の問題数（5〜15）。1問≒1分で 5〜10 分 |
| category_ids | [] | 空 = 全分野 |
| font_scale | 1.0 | 0.9 / 1.0 / 1.15 / 1.3 |
| theme | "auto" | auto / light / dark |
| target_sec | 90 | 1 問の目標時間（本試験 90分/60問=90秒） |

## 3. 出題アルゴリズム（selection.py）

`pick_study(conn, size, include_calc, category_ids, rng)`:
- 母集団: `pool='study'`、`include_calc=false` なら `is_calc=0`、category_ids 指定時はその分野のみ。
- 各問題の状態を最新 attempt から判定:
  - `wrong`: 最新回答が不正解 → 最優先（ただし同日に既に出たものは後回し）
  - `unseen`: 未回答
  - `slow`: 最新正解だが time_ms > target_sec*1000
  - `mastered`: 最新正解かつ目標時間内
- 枠配分（size に対する割合、足りなければ次の枠で埋める）: wrong 30% → unseen 50%（正答率の低い分野を重み付け）→ slow 10% → mastered 10%（最終回答が古い順）。
- 同一セッション内で重複なし。順序はシャッフル。
- `pick_review`: 間違えた問題のみ（最新が不正解）。
- `pick_mock(conn, exam_code=None, size=20)`: `pool='mock'` のみ。exam_code 指定時はその回を番号順に全問、未指定は mock プールからランダム size 問。time_limit_sec = 問題数 × 90。include_calc 設定は模試には適用しない（本番どおり）。

## 4. REST API（JSON、プレフィックス /api）

エラーは `{"error": "..."}` + 4xx。

### GET /api/home
起動直後に 1 回で必要な情報を返す（操作回数最小化）。
```json
{"active_session": {"id":3,"mode":"study","current_index":2,"total":8} | null,
 "today": {"answered": 12, "correct": 9, "time_ms": 640000},
 "streak_days": 4,
 "settings": {...全設定...},
 "pool": {"study": 1820, "study_unseen": 1500, "mock": 80, "review": 23},
 "weak_categories": [{"id":11,"name":"セキュリティ","accuracy":0.52}]   // 最大3
}
```

### POST /api/sessions
body: `{"mode":"study"|"review"|"mock", "exam_code"?: "07_haru", "size"?: 8, "category_ids"?: [11]}`
（size・category_ids・計算設定は省略時 settings を使用。category_ids は今回のセッションだけの上書きで settings は変えない。成績画面の「この分野を学習」用）。既存の active セッションは `abandoned` にする。
返り値: セッションオブジェクト（下記）。出題できる問題が 0 件なら 409。

### GET /api/sessions/active → セッションオブジェクト or `null`
### GET /api/sessions/{id} → セッションオブジェクト

セッションオブジェクト:
```json
{"id":3,"mode":"study","status":"active","current_index":2,"current_elapsed_ms":15300,
 "time_limit_sec":null,"started_at":"...","remaining_sec":null,
 "questions":[
   {"id":"fe-01_aki-q01","exam_label":"令和元年秋期","number":1,
    "category_id":1,"category_name":"基礎理論","body":"...","choices":[...],
    "image_url":"/media/01_aki/q01.png"|null,"is_calc":true,
    "source_label":"...","explanation_url":"...",
    "attempt": null | {"chosen":"イ","is_correct":false,"time_ms":42000,"answer":"ア"}}
 ]}
```
- 正解 `answer` は回答済み問題の attempt 内にのみ含める。模試モードは finish まで `answer`/`is_correct` を含めない（attempt は `{"chosen":"イ","time_ms":...}` のみ）。

### POST /api/sessions/{id}/answer
body: `{"question_id":"...","chosen":"ア","time_ms":42000}` → 即時に attempts へ保存（オートセーブ）。
同じ問題への再送は上書き（UPSERT）。current_index を次へ進め current_elapsed_ms=0。
返り値: study/review `{"is_correct":true,"answer":"ア","explanation_url":"..."}`、mock `{"saved":true}`。

### POST /api/sessions/{id}/progress
body: `{"current_index":2,"current_elapsed_ms":15300}` → オートセーブ（フロントが 5 秒毎・visibilitychange 時に送る）。`navigator.sendBeacon` でも受けられるよう text/plain の JSON も受理する。

### POST /api/sessions/{id}/finish
未回答の問題（模試）は chosen=NULL, is_correct=0, time_ms=0 で記録。status='done'。
返り値: `{"total":8,"correct":6,"time_ms":410000,"avg_time_ms":51250,
"by_category":[{"category_id":1,"name":"基礎理論","total":2,"correct":1}],
"questions":[...answer 付き...], "passed": true|null}`（passed は模試のみ、正答率 60% 以上）。

### GET /api/settings / PUT /api/settings（部分更新、未知キーは 400）

### GET /api/stats/categories
```json
{"fields":[{"field":"te","name":"テクノロジ系","categories":[
  {"id":1,"name":"基礎理論","major_no":1,"major_name":"基礎理論",
   "total":172,"seen":40,"attempts":55,"correct":38,"accuracy":0.69,
   "avg_time_ms":61000,"mastered":30}]}]}
```
total は study プール数。accuracy は各問題の **最新** 回答で算出（attempts/correct は延べ）。

### GET /api/stats/time
```json
{"overall":{"avg_ms":58000,"median_ms":52000,"over_target_rate":0.18},
 "by_category":[{"id":1,"name":"基礎理論","avg_ms":72000,"median_ms":65000,"n":55}],
 "calc_vs_noncalc":{"calc":{"avg_ms":95000,"accuracy":0.55,"n":20},"noncalc":{...}},
 "daily":[{"date":"2026-11-20","n":16,"avg_ms":60000,"accuracy":0.75}],   // 直近30日
 "slowest":[{"question_id":"...","label":"令和元年秋期 問3","category_name":"...","time_ms":180000,"is_correct":false}],  // 10件
 "speed_accuracy":[{"bucket":"〜30秒","n":10,"accuracy":0.9}, ...]   // 0-30,30-60,60-90,90-120,120+
}
```

### GET /api/history?limit=50&offset=0
回答履歴（新しい順）: `[{"question_id","label","category_name","chosen","answer","is_correct","time_ms","answered_at","mode"}]`

### GET /api/questions/{id}
単問（answer 込み、最新 attempt 一覧つき）。履歴から開く用。模試プールの問題は、その問題を含む模試を finish 済みの場合のみ answer を返す。

### GET /media/<path>
`data/images/` 配下の画像を配信（パストラバーサル防止）。

### GET /
`static/index.html`（SPA）。`/static/*` は静的ファイル。

## 5. UI 要件（static/）

- **起動 → 1 タップで学習開始**: ホーム最上部に大きな主ボタン。active セッションがあれば「続きから（3/8）」、なければ「学習を始める（8問・約8分）」。設定はホームの下部/設定タブで。
- 1 回 5〜10 分（既定 8 問）。画面上部に進捗バーと経過時間（目標時間超過で色が穏やかに変化、急かさない）。
- 回答: 選択肢ボタンは画面下半分（親指で届く位置）、最小高さ 52px。回答後すぐ正誤・正解・「過去問道場で解説」リンク、「次へ」。
- 図がある問題は画像を幅 100% 表示、タップで拡大（ピンチズーム可能な全画面表示）。
- オートセーブ: 回答は即 POST、経過時間は 5 秒毎と `visibilitychange`/`pagehide` で sendBeacon。localStorage にも未送信キューを保持し、オンライン復帰時に再送（通学中の電波断対策）。
- 下部タブ: ホーム / 成績 / 履歴 / 設定。
- 成績: 分野別一覧（テクノロジ/マネジメント/ストラテジ → 23 中分類、正答率バー、学習済/総数、平均時間）。時間分析（平均・中央値、分野別平均時間、計算 vs 非計算、速度帯別正答率、日別推移、遅かった問題）。グラフは素の SVG/CSS（外部 CDN なし）。
- 設定: 計算問題を含める ON/OFF、問題数、分野選択（画像と同じ 3 系統＋23 中分類のチェックボックス、系統で一括）、文字サイズ、テーマ。変更は即保存（オートセーブ）。
- 模擬試験（令和5〜8年）: ホームの副ボタン「模擬試験」→ 回を選択 or ランダム 20 問。制限時間カウントダウン、終了まで正誤非表示、終了後に結果と合否目安（60%）。
- 復習: 「間違えた問題（23）」ボタン。
- 目に優しい配色: ライト = 背景 #f4f1ea・文字 #33302b、ダーク = 背景 #1f2124・文字 #d6d3cc。純白/純黒を避け、コントラストは WCAG AA 以上を維持。正解=落ち着いた緑 #4f8a5b、不正解=くすんだ赤 #b5564b。本文 17px 基準、行間 1.8、本文最大幅 40em。システムフォント（"Hiragino Sans","Noto Sans JP",sans-serif）。
- スマホ: viewport 設定、`env(safe-area-inset-*)`、ホーム画面追加用 manifest。ビルド不要の素の HTML/CSS/JS（ES2020）。
