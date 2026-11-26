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
}


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
