import json
import os
import sqlite3
from pathlib import Path

from .categories import CATEGORIES

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
DEFAULT_DB = DATA_DIR / "fe.db"

DEFAULT_SETTINGS = {
    "include_calc": True,
    "session_size": 8,
    "category_ids": [],
    "font_scale": 1.0,
    "theme": "auto",
    "target_sec": 90,
    "session_size_b": 2,
    "target_sec_b": 300,
    "b_topics": [],
}

# 科目B 対応で questions に追加する列（SPEC 6.1）。既存 DB へは init_db が冪等に ALTER する
B_COLUMNS = [
    ("part", "TEXT NOT NULL DEFAULT 'A'"),
    ("images", "TEXT"),
    ("group_id", "TEXT"),
    ("sub_label", "TEXT"),
    ("b_topic", "TEXT"),
    ("tags", "TEXT"),
    ("difficulty", "TEXT"),
    ("explanation_images", "TEXT"),
    ("explanation_source", "TEXT"),
]


def migrate(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    for name, decl in B_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {name} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_q_part ON questions(part, pool)")


def resolve_db_path(db_path=None):
    return str(db_path or os.environ.get("FE_DB") or DEFAULT_DB)


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
        migrate(conn)
        for c in CATEGORIES:
            conn.execute(
                "INSERT INTO categories(id,field,major_no,major_name,name) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET field=excluded.field, major_no=excluded.major_no, "
                "major_name=excluded.major_name, name=excluded.name",
                (c["id"], c["field"], c["major_no"], c["major_name"], c["name"]),
            )
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v)))
        conn.commit()
    finally:
        conn.close()


def get_settings(conn):
    s = dict(DEFAULT_SETTINGS)
    for r in conn.execute("SELECT key,value FROM settings"):
        if r["key"] in s:
            s[r["key"]] = json.loads(r["value"])
    return s


def set_settings(conn, values):
    for k, v in values.items():
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, json.dumps(v)),
        )
    conn.commit()
