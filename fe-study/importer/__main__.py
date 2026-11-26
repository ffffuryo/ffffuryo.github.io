"""FE 科目A（旧午前）問題インポーター。

    cd fe-study
    python -m importer                          # 全回を取得・解析して data/fe.db に UPSERT
    python -m importer --years 31_haru,01_aki   # 指定した回だけ
    python -m importer --limit 5                # 先頭から 5 回分だけ
    python -m importer --offline                # ネットワークに出ず data/cache/ のキャッシュだけで実行
    python -m importer --list                   # 対象の回の一覧を表示して終了

取得: IPA（www.ipa.go.jp）のみ。1 秒以上の間隔、User-Agent は fe-study-importer/1.0、PDF は data/cache/ にキャッシュ。
fe-siken.com にはアクセスしない（解説 URL は規則で生成するだけ）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # fe-study/
sys.path.insert(0, str(ROOT))

from app.categories import CATEGORIES  # noqa: E402

from . import http_cache  # noqa: E402
from .calc_detect import is_calc  # noqa: E402
from .classify import classify_exam  # noqa: E402
from .ipa_fetch import Exam, discover_all, download  # noqa: E402
from .pdf_parse import parse_exam  # noqa: E402

DEFAULT_DB = ROOT / "data" / "fe.db"
IMAGES_DIR = ROOT / "data" / "images"
LOW_CONFIDENCE = 0.5

# app/schema.sql が無い場合のフォールバック（docs/SPEC.md のスキーマと同一）
FALLBACK_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY, field TEXT NOT NULL, major_no INTEGER NOT NULL,
  major_name TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, exam_code TEXT NOT NULL, exam_label TEXT NOT NULL, number INTEGER NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id), body TEXT NOT NULL, choices TEXT NOT NULL,
  answer TEXT NOT NULL, image_path TEXT, is_calc INTEGER NOT NULL DEFAULT 0,
  pool TEXT NOT NULL CHECK (pool IN ('study','mock')), source_label TEXT NOT NULL,
  source_url TEXT NOT NULL, explanation_url TEXT NOT NULL,
  parse_confidence REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_q_pool_cat ON questions(pool, category_id);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL CHECK (mode IN ('study','review','mock')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','abandoned')),
  question_ids TEXT NOT NULL, current_index INTEGER NOT NULL DEFAULT 0,
  current_elapsed_ms INTEGER NOT NULL DEFAULT 0, time_limit_sec INTEGER,
  options TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')), ended_at TEXT);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES sessions(id),
  question_id TEXT NOT NULL REFERENCES questions(id), chosen TEXT, is_correct INTEGER NOT NULL,
  time_ms INTEGER NOT NULL, answered_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(session_id, question_id));
CREATE INDEX IF NOT EXISTS idx_att_q ON attempts(question_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def init_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from app.db import init_db  # バックエンド担当の実装があればそれを使う
    except Exception:
        init_db = None
    if init_db is not None:
        init_db(str(db_path))
        return
    schema_file = ROOT / "app" / "schema.sql"
    sql = schema_file.read_text(encoding="utf-8") if schema_file.exists() else FALLBACK_SCHEMA
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        for c in CATEGORIES:
            conn.execute(
                "INSERT INTO categories(id,field,major_no,major_name,name) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET field=excluded.field, major_no=excluded.major_no, "
                "major_name=excluded.major_name, name=excluded.name",
                (c["id"], c["field"], c["major_no"], c["major_name"], c["name"]))
        conn.commit()
    finally:
        conn.close()


UPSERT_SQL = """
INSERT INTO questions (id, exam_code, exam_label, number, category_id, body, choices, answer, image_path,
                       is_calc, pool, source_label, source_url, explanation_url, parse_confidence)
VALUES (:id, :exam_code, :exam_label, :number, :category_id, :body, :choices, :answer, :image_path,
        :is_calc, :pool, :source_label, :source_url, :explanation_url, :parse_confidence)
ON CONFLICT(id) DO UPDATE SET
  exam_code=excluded.exam_code, exam_label=excluded.exam_label, number=excluded.number,
  category_id=excluded.category_id, body=excluded.body, choices=excluded.choices, answer=excluded.answer,
  image_path=excluded.image_path, is_calc=excluded.is_calc, pool=excluded.pool,
  source_label=excluded.source_label, source_url=excluded.source_url,
  explanation_url=excluded.explanation_url, parse_confidence=excluded.parse_confidence
"""


def build_rows(exam: Exam, images_dir: Path, use_ocr: bool = True):
    qs_pdf, ans_pdf = download(exam)
    questions, answers, warnings = parse_exam(qs_pdf, ans_pdf, exam.code, images_dir, use_ocr=use_ocr)
    items = []
    for q in questions:
        text = "\n".join([q.body] + [c["text"] for c in q.choices] + [q.aux_text])
        field = answers.get(q.number, (None, None))[1]
        items.append({"number": q.number, "text": text, "field": field})
    cats = classify_exam(items)
    rows = []
    for q, cat in zip(questions, cats):
        if q.number not in answers:
            warnings.append(f"{exam.code} 問{q.number}: 正解が見つからない")
            continue
        calc_text = q.body or q.aux_text
        calc_choices = [c["text"] for c in q.choices]
        rows.append({
            "id": f"fe-{exam.code}-q{q.number:02d}",
            "exam_code": exam.code,
            "exam_label": exam.label,
            "number": q.number,
            "category_id": int(cat),
            "body": q.body,
            "choices": json.dumps(q.choices, ensure_ascii=False),
            "answer": answers[q.number][0],
            "image_path": q.image_path,
            "is_calc": 1 if is_calc(calc_text, calc_choices) else 0,
            "pool": exam.pool,
            "source_label": exam.source_label(q.number),
            "source_url": exam.qs_url,
            "explanation_url": exam.explanation_url(q.number),
            "parse_confidence": float(q.parse_confidence),
            "_text_source": q.text_source,
        })
    missing = sorted(set(answers) - {q.number for q in questions})
    if missing:
        warnings.append(f"{exam.code}: 問題を切り出せなかった問 {missing}")
    return rows, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m importer", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", help="取り込む回コードをカンマ区切りで（例 21_haru,01_aki,05_haru）")
    ap.add_argument("--limit", type=int, help="先頭から N 回分だけ取り込む")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB のパス（既定 data/fe.db）")
    ap.add_argument("--images", default=str(IMAGES_DIR), help="画像の出力先（既定 data/images）")
    ap.add_argument("--offline", nargs="?", const=True, default=False, metavar="CACHE_DIR",
                    help="ネットワークに出ずキャッシュだけで実行（CACHE_DIR 指定時はそこをキャッシュとして使う）")
    ap.add_argument("--no-menjo", action="store_true", help="令和2〜4年度の免除試験を除外")
    ap.add_argument("--no-sample", action="store_true", help="科目A サンプル問題を除外")
    ap.add_argument("--no-ocr", action="store_true", help="tesseract があっても OCR しない")
    ap.add_argument("--list", action="store_true", help="対象の回を表示して終了")
    a = ap.parse_args(argv)

    if a.offline:
        http_cache.OFFLINE = True
        if isinstance(a.offline, str):
            http_cache.CACHE_DIR = Path(a.offline)

    exams = discover_all(include_menjo=not a.no_menjo, include_sample=not a.no_sample)
    if a.years:
        want = [y.strip() for y in a.years.split(",") if y.strip()]
        unknown = set(want) - {e.code for e in exams}
        if unknown:
            print("未知の回コード:", ", ".join(sorted(unknown)), file=sys.stderr)
        exams = [e for e in exams if e.code in want]
    if a.limit:
        exams = exams[: a.limit]
    if a.list:
        for e in exams:
            print(f"{e.code:<10} {e.label:<14} {e.kind:<7} pool={e.pool:<5} {e.qs_url}")
        return 0
    if not exams:
        print("対象の回がありません", file=sys.stderr)
        return 1

    db_path = Path(a.db)
    images_dir = Path(a.images)
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")

    per_exam = {}
    all_rows = []
    all_warnings = []
    errors = []
    t0 = time.time()
    for e in exams:
        t = time.time()
        try:
            rows, warns = build_rows(e, images_dir, use_ocr=not a.no_ocr)
        except Exception as ex:  # 1 回分の失敗で全体を止めない
            errors.append(f"{e.code}: {type(ex).__name__}: {ex}")
            print(f"[NG] {e.code} {e.label}: {ex}", flush=True)
            continue
        with conn:
            conn.executemany(UPSERT_SQL, [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
        per_exam[e.code] = (e, rows)
        all_rows.extend(rows)
        all_warnings.extend(warns)
        n_img = sum(1 for r in rows if r["image_path"])
        print(f"[OK] {e.code:<9} {e.label:<12} {len(rows):>3}問  画像{n_img:>3}  "
              f"計算{sum(r['is_calc'] for r in rows):>3}  {time.time() - t:5.1f}s", flush=True)
        for w in warns:
            print("     WARN", w, flush=True)
    conn.close()

    # ---- サマリ ----
    print("\n==== サマリ ====")
    print(f"対象 {len(exams)} 回 / 成功 {len(per_exam)} 回 / 取り込み {len(all_rows)} 問 / {time.time() - t0:.0f}s")
    print("\n回ごと件数:")
    for code, (e, rows) in per_exam.items():
        src = Counter(r["_text_source"] for r in rows)
        print(f"  {code:<9} {e.label:<14} {len(rows):>3}問  pool={e.pool:<5} "
              f"テキスト={src.get('pdf', 0) + src.get('ocr', 0):>3} 画像のみ={src.get('image', 0):>3}")
    pools = Counter(r["pool"] for r in all_rows)
    print("\npool別件数:", dict(pools))
    dist = Counter(r["category_id"] for r in all_rows)
    print("\n分類分布（今回取り込み分）:")
    for c in CATEGORIES:
        print(f"  {c['id']:>2} {c['name']:<16} {dist.get(c['id'], 0):>4}")
    print("\nis_calc 件数:", sum(r["is_calc"] for r in all_rows))
    low = [r for r in all_rows if r["parse_confidence"] < LOW_CONFIDENCE]
    print(f"低confidence (< {LOW_CONFIDENCE}) 件数:", len(low))
    for r in low[:20]:
        print("   ", r["id"], r["parse_confidence"])
    imgonly = sum(1 for r in all_rows if r["_text_source"] == "image")
    if imgonly:
        print(f"\n画像のみ（スキャン PDF、問題文テキストなし）: {imgonly} 問。"
              "分類は系統（解答例の分野/問題番号）と位置による推定、is_calc は判定不可のため 0。"
              "tesseract-ocr + jpn を入れて再実行すると OCR テキストで分類・計算判定を補える。")
    if all_warnings:
        print("\n警告:", len(all_warnings))
        for w in all_warnings:
            print("  ", w)
    if errors:
        print("\nエラー:")
        for er in errors:
            print("  ", er)
    # DB 全体の件数
    conn = sqlite3.connect(db_path)
    tot = dict(conn.execute("SELECT pool, COUNT(*) FROM questions GROUP BY pool").fetchall())
    conn.close()
    print("\nDB 全体の pool 別件数:", tot)
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
