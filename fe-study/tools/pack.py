"""スタンドアロン版のデータパックを作る（SPEC 7.2）。

    cd fe-study && python -m tools.pack [--db data/fe.db] [--out dist] [--images data/images]

出力:
    dist/fe-pack-public.zip   IPA 由来の問題のみ（公開可）
    dist/fe-pack-private.zip  書籍付録（furoku*）由来の問題 + IPA 問題への追記 patches（私的使用のみ・公開禁止）

zip は ZIP_STORED（無圧縮）。画像は 16 階調 4bit パレット PNG に再エンコードして小さくする。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "data" / "fe.db"
DEFAULT_IMAGES = BASE_DIR / "data" / "images"
DEFAULT_OUT = BASE_DIR / "dist"

PUBLIC_NAME = "fe-pack-public.zip"
PRIVATE_NAME = "fe-pack-private.zip"

# SPEC 2章 questions + 6.1 追加列。古い DB にない列は null（part だけは schema の DEFAULT 'A' に合わせる）
QUESTION_COLUMNS = [
    "id", "exam_code", "exam_label", "number", "category_id", "body", "choices", "answer",
    "image_path", "is_calc", "pool", "source_label", "source_url", "explanation_url",
    "parse_confidence", "created_at",
    "part", "images", "group_id", "sub_label", "b_topic", "tags", "difficulty",
    "explanation_images", "explanation_source",
]
JSON_COLUMNS = ("choices", "images", "tags", "explanation_images")
MISSING_DEFAULTS = {"part": "A"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json_compact(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_pretty(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


# ---------- DB 読み込み ----------

def load_db(db_path: Path) -> tuple[list[dict], list[dict]]:
    """(questions, categories) を返す。JSON 列は配列に、is_calc は bool にデコード済み。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
        select = [c for c in QUESTION_COLUMNS if c in have]
        rows = conn.execute(f"SELECT {', '.join(select)} FROM questions ORDER BY id").fetchall()
        categories = [dict(r) for r in conn.execute(
            "SELECT id, field, major_no, major_name, name FROM categories ORDER BY id")]
    finally:
        conn.close()

    questions = []
    for r in rows:
        raw = dict(r)
        q = {}
        for col in QUESTION_COLUMNS:
            if col in raw:
                v = raw[col]
            else:
                v = MISSING_DEFAULTS.get(col)
            if col in JSON_COLUMNS and isinstance(v, str):
                v = json.loads(v) if v.strip() else None
            elif col == "is_calc" and v is not None:
                v = bool(v)
            q[col] = v
        questions.append(q)
    return questions, categories


# ---------- 振り分け ----------

def is_furoku(q: dict) -> bool:
    return str(q.get("exam_code") or "").startswith("furoku")


def public_row(q: dict) -> dict:
    """公開パック用の行。書籍由来の解説・タグ・難易度は空にする（B だけでなく IPA 問題も同様に、追記は private の patches で配る）。"""
    row = dict(q)
    row["explanation_images"] = []
    row["explanation_source"] = None
    row["tags"] = []
    row["difficulty"] = None
    return row


def make_patch(q: dict) -> dict | None:
    ei = q.get("explanation_images") or []
    tags = q.get("tags") or []
    diff = q.get("difficulty")
    if not (ei or tags or diff):
        return None
    return {
        "id": q["id"],
        "explanation_images": ei,
        "explanation_source": q.get("explanation_source"),
        "tags": tags,
        "difficulty": diff,
    }


def split(questions: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(public_rows, private_rows, patches) に振り分ける。"""
    public_rows, private_rows, patches = [], [], []
    for q in questions:
        if is_furoku(q):
            private_rows.append(q)
        else:
            public_rows.append(public_row(q))
            p = make_patch(q)
            if p is not None:
                patches.append(p)
    return public_rows, private_rows, patches


# ---------- 画像 ----------

def encode_image(src: Path) -> bytes:
    """16 階調グレースケールの 4bit パレット PNG に再エンコードする。"""
    with Image.open(src) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            bg.alpha_composite(rgba)
            im = bg
        pal = im.convert("L").quantize(colors=16)
        buf = io.BytesIO()
        pal.save(buf, "PNG", optimize=True, bits=4)
        return buf.getvalue()


def _refs(q: dict, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for k in keys:
        v = q.get(k)
        if not v:
            continue
        if isinstance(v, str):
            out.append(v)
        else:
            out.extend(x for x in v if isinstance(x, str) and x)
    return out


def collect_images(refs, image_dir: Path) -> dict[str, bytes]:
    """相対パス → 再エンコード済み PNG バイト列。見つからない画像は警告して除外する。"""
    out: dict[str, bytes] = {}
    for rel in sorted(set(refs)):
        p = PurePosixPath(rel)
        if not rel or p.is_absolute() or ".." in p.parts:
            _log(f"警告: 不正な画像パスを無視: {rel}")
            continue
        src = image_dir.joinpath(*p.parts)
        if not src.is_file():
            _log(f"警告: 画像が見つからないため除外: {rel}")
            continue
        out[rel] = encode_image(src)
    return out


# ---------- zip 書き出し ----------

def write_zip(path: Path, kind: str, categories, questions, patches, images, created_at) -> dict:
    manifest = {
        "format": "fe-pack",
        "version": 1,
        "kind": kind,
        "created_at": created_at,
        "question_count": len(questions),
        "patch_count": len(patches),
        "image_count": len(images),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", _json_pretty(manifest), compress_type=zipfile.ZIP_STORED)
        z.writestr("categories.json", _json_compact(categories), compress_type=zipfile.ZIP_STORED)
        z.writestr("questions.json", _json_compact(questions), compress_type=zipfile.ZIP_STORED)
        if kind == "private":
            z.writestr("patches.json", _json_compact(patches), compress_type=zipfile.ZIP_STORED)
        for rel, data in images.items():
            z.writestr(f"images/{rel}", data, compress_type=zipfile.ZIP_STORED)
    os.replace(tmp, path)
    manifest["size"] = path.stat().st_size
    return manifest


def build_packs(db_path: Path, out_dir: Path, image_dir: Path) -> dict:
    """パックを書き出して要約（件数・サイズ）を返す。private 対象が 0 件なら private は作らない。"""
    questions, categories = load_db(db_path)
    public_rows, private_rows, patches = split(questions)
    created_at = _now()
    summary: dict = {"public": None, "private": None}

    pub_refs = []
    for q in public_rows:
        pub_refs += _refs(q, ("image_path", "images"))
    pub_images = collect_images(pub_refs, image_dir)
    pub_path = out_dir / PUBLIC_NAME
    summary["public"] = {**write_zip(pub_path, "public", categories, public_rows, [], pub_images, created_at),
                         "path": pub_path}

    priv_path = out_dir / PRIVATE_NAME
    if private_rows or patches:
        priv_refs = []
        for q in private_rows:
            priv_refs += _refs(q, ("image_path", "images", "explanation_images"))
        for p in patches:
            priv_refs += _refs(p, ("explanation_images",))
        priv_images = collect_images(priv_refs, image_dir)
        summary["private"] = {**write_zip(priv_path, "private", categories, private_rows, patches,
                                          priv_images, created_at), "path": priv_path}
    else:
        if priv_path.exists():
            priv_path.unlink()
        print("private 対象（furoku 問題・patches）が 0 件のため fe-pack-private.zip は作成しません。")
    return summary


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    return f"{n / 1024:.1f} KB"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="スタンドアロン版のデータパックを書き出す（SPEC 7.2）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB（既定: data/fe.db）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="出力先ディレクトリ（既定: dist）")
    ap.add_argument("--images", default=str(DEFAULT_IMAGES), help="画像ディレクトリ（既定: data/images）")
    args = ap.parse_args(argv)

    db = Path(args.db).resolve()
    if not db.is_file():
        _log(f"DB が見つかりません: {db}")
        return 1
    out = Path(args.out).resolve()
    s = build_packs(db, out, Path(args.images).resolve())

    pub = s["public"]
    print(f"public : 問題 {pub['question_count']} 件 / 画像 {pub['image_count']} 枚 / "
          f"{_fmt_size(pub['size'])} → {pub['path']}")
    priv = s["private"]
    if priv:
        print(f"private: 問題 {priv['question_count']} 件 / patches {priv['patch_count']} 件 / "
              f"画像 {priv['image_count']} 枚 / {_fmt_size(priv['size'])} → {priv['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
