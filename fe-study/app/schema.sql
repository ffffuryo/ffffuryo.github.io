CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  field TEXT NOT NULL,
  major_no INTEGER NOT NULL,
  major_name TEXT NOT NULL,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  exam_code TEXT NOT NULL,
  exam_label TEXT NOT NULL,
  number INTEGER NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  body TEXT NOT NULL,
  choices TEXT NOT NULL,
  answer TEXT NOT NULL,
  image_path TEXT,
  is_calc INTEGER NOT NULL DEFAULT 0,
  pool TEXT NOT NULL CHECK (pool IN ('study','mock')),
  source_label TEXT NOT NULL,
  source_url TEXT NOT NULL,
  explanation_url TEXT NOT NULL,
  parse_confidence REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_q_pool_cat ON questions(pool, category_id);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL CHECK (mode IN ('study','review','mock')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','abandoned')),
  question_ids TEXT NOT NULL,
  current_index INTEGER NOT NULL DEFAULT 0,
  current_elapsed_ms INTEGER NOT NULL DEFAULT 0,
  time_limit_sec INTEGER,
  options TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  question_id TEXT NOT NULL REFERENCES questions(id),
  chosen TEXT,
  is_correct INTEGER NOT NULL,
  time_ms INTEGER NOT NULL,
  answered_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE(session_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_att_q ON attempts(question_id);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
