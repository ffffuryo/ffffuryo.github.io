"""tools.pack / tools.build_site の検証（書籍・IPA の実データは使わない。自作のダミーのみ）。

    cd fe-study && python -m pytest -q tests/test_pack.py
"""
import io
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import init_db  # noqa: E402
from tools import build_site, pack  # noqa: E402

PUBLIC_IDS = {"fe-01_aki-q01", "fe-01_aki-q02", "feb-sample-q01", "feb-sample-q02"}
PRIVATE_IDS = {"feb-furoku2-q01"}


def _png(path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (96, 64), (255, 255, 255))
    for x in range(96):
        for y in range(64):
            im.putpixel((x, y), ((x * (seed + 3)) % 256, (y * 5) % 256, (x + y + seed) % 256))
    im.save(path)


def _ins(conn, qid, exam, num, cat, pool="study", *, part="A", image=None, images=None,
         expl=None, expl_src=None, tags=None, diff=None, group=None, sub=None, topic=None):
    conn.execute(
        "INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,image_path,"
        "is_calc,pool,source_label,source_url,explanation_url,part,images,group_id,sub_label,b_topic,"
        "tags,difficulty,explanation_images,explanation_source) "
        "VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, exam, "テスト回", num, cat, f"【テスト】{qid}",
         json.dumps([{"label": "ア", "text": "a"}, {"label": "イ", "text": "b"}], ensure_ascii=False),
         "ア", image, pool, "【テスト】出典", "http://src", "http://exp" if part == "A" else "",
         part, json.dumps(images) if images else None, group, sub, topic,
         json.dumps(tags, ensure_ascii=False) if tags else None, diff,
         json.dumps(expl) if expl else None, expl_src))


@pytest.fixture
def env(tmp_path):
    """一時 DB（SPEC 2章 + 6.1 のスキーマ）と一時画像ディレクトリ。"""
    db = tmp_path / "fe.db"
    init_db(str(db))
    images = tmp_path / "images"
    for i, rel in enumerate(["01_aki/q01.png", "01_aki/q02.png", "b/sample/q01_1.png",
                             "b/sample/q02_1.png", "b/furoku2/q01_1.png", "bx/sample-q01-e1.png",
                             "bx/furoku2-q01-e1.png"]):
        _png(images / rel, i)
    conn = sqlite3.connect(str(db))
    _ins(conn, "fe-01_aki-q01", "01_aki", 1, 1, image="01_aki/q01.png")
    _ins(conn, "fe-01_aki-q02", "01_aki", 2, 2, image=None)
    # 科目B（IPA 公開問題）: 解説・タグ・難易度は書籍由来 → public では空、private の patches へ
    _ins(conn, "feb-sample-q01", "sample", 1, 2, part="B", images=["b/sample/q01_1.png"],
         expl=["bx/sample-q01-e1.png"], expl_src="【テスト】付録1", tags=["再帰", "トレース"],
         diff="ふつう", topic="pseudo")
    _ins(conn, "feb-sample-q02", "sample", 2, 2, part="B", images=["b/sample/q02_1.png"],
         topic="pseudo")
    # 書籍付録由来（furoku）: 全部 private
    _ins(conn, "feb-furoku2-q01", "furoku2", 1, 11, part="B", images=["b/furoku2/q01_1.png"],
         expl=["bx/furoku2-q01-e1.png"], expl_src="【テスト】付録2", tags=["セキュリティ"],
         diff="難しい", topic="security")
    conn.commit()
    conn.close()
    return db, images, tmp_path / "dist"


def _read_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
        questions = json.loads(z.read("questions.json"))
        patches = json.loads(z.read("patches.json")) if "patches.json" in names else None
        imgs = {n: z.read(n) for n in names if n.startswith("images/")}
        types = {i.compress_type for i in z.infolist()}
    return manifest, questions, patches, imgs, types


def _pack(env):
    db, images, out = env
    pack.main(["--db", str(db), "--out", str(out), "--images", str(images)])
    return out / "fe-pack-public.zip", out / "fe-pack-private.zip"


def test_public_excludes_furoku(env):
    pub, _ = _pack(env)
    _, questions, patches, _, _ = _read_zip(pub)
    assert patches is None
    assert {q["id"] for q in questions} == PUBLIC_IDS
    assert not any(q["exam_code"].startswith("furoku") for q in questions)


def test_public_b_book_fields_empty(env):
    pub, _ = _pack(env)
    _, questions, _, imgs, _ = _read_zip(pub)
    for q in questions:
        if q["part"] == "B":
            assert q["explanation_images"] == []
            assert q["explanation_source"] is None
            assert q["tags"] == []
            assert q["difficulty"] is None
    # 公開パックの画像は IPA 問題の image_path / images のみ
    assert set(imgs) == {"images/01_aki/q01.png", "images/b/sample/q01_1.png", "images/b/sample/q02_1.png"}


def test_private_has_furoku_and_patches(env):
    _, priv = _pack(env)
    manifest, questions, patches, imgs, _ = _read_zip(priv)
    assert manifest["kind"] == "private"
    assert {q["id"] for q in questions} == PRIVATE_IDS
    assert len(patches) == 1
    p = patches[0]
    assert p["id"] == "feb-sample-q01"
    assert p["explanation_images"] == ["bx/sample-q01-e1.png"]
    assert p["explanation_source"] == "【テスト】付録1"
    assert p["tags"] == ["再帰", "トレース"]
    assert p["difficulty"] == "ふつう"
    # 解説画像（furoku 行 + patches）も入る
    assert "images/bx/sample-q01-e1.png" in imgs
    assert "images/bx/furoku2-q01-e1.png" in imgs
    assert "images/b/furoku2/q01_1.png" in imgs


def test_zip_is_stored(env):
    pub, priv = _pack(env)
    for path in (pub, priv):
        _, _, _, _, types = _read_zip(path)
        assert types == {zipfile.ZIP_STORED}


def test_manifest_counts(env):
    pub, priv = _pack(env)
    m, questions, _, imgs, _ = _read_zip(pub)
    assert m["format"] == "fe-pack" and m["version"] == 1 and m["kind"] == "public"
    assert m["question_count"] == len(questions) == len(PUBLIC_IDS)
    assert m["patch_count"] == 0
    assert m["image_count"] == len(imgs) == 3
    assert isinstance(m["created_at"], str) and m["created_at"]
    m, questions, patches, imgs, _ = _read_zip(priv)
    assert m["kind"] == "private"
    assert m["question_count"] == len(questions) == 1
    assert m["patch_count"] == len(patches) == 1
    assert m["image_count"] == len(imgs) == 3


def test_images_are_4bit_palette_png(env):
    pub, _ = _pack(env)
    _, _, _, imgs, _ = _read_zip(pub)
    for name, data in imgs.items():
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert data[24] == 4, f"bit depth != 4: {name}"   # IHDR bit depth
        assert data[25] == 3, f"color type != palette: {name}"
        with Image.open(io.BytesIO(data)) as im:
            assert im.mode == "P"
            assert len(im.getpalette()) // 3 <= 16


def test_private_skipped_when_nothing_private(tmp_path, capsys):
    db = tmp_path / "fe.db"
    init_db(str(db))
    conn = sqlite3.connect(str(db))
    _ins(conn, "fe-01_aki-q01", "01_aki", 1, 1)
    conn.commit()
    conn.close()
    out = tmp_path / "dist"
    out.mkdir()
    (out / "fe-pack-private.zip").write_bytes(b"stale")
    pack.main(["--db", str(db), "--out", str(out), "--images", str(tmp_path / "none")])
    assert not (out / "fe-pack-private.zip").exists()
    assert (out / "fe-pack-public.zip").exists()
    assert "作成しません" in capsys.readouterr().out


def test_missing_image_warns_and_continues(env, capsys):
    db, images, out = env
    conn = sqlite3.connect(str(db))
    _ins(conn, "fe-01_aki-q09", "01_aki", 9, 3, image="01_aki/missing.png")
    conn.commit()
    conn.close()
    pub, _ = _pack(env)
    err = capsys.readouterr().err
    assert "01_aki/missing.png" in err
    _, questions, _, imgs, _ = _read_zip(pub)
    assert "fe-01_aki-q09" in {q["id"] for q in questions}
    assert "images/01_aki/missing.png" not in imgs


def test_old_db_without_b_columns(tmp_path):
    """part 等の列が無い古い DB でも落ちず、列は null（part は既定 'A'）。"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE categories(id INTEGER PRIMARY KEY, field TEXT, major_no INTEGER, major_name TEXT, name TEXT);"
        "INSERT INTO categories VALUES(1,'te',1,'大','小');"
        "CREATE TABLE questions(id TEXT PRIMARY KEY, exam_code TEXT, exam_label TEXT, number INTEGER,"
        " category_id INTEGER, body TEXT, choices TEXT, answer TEXT, image_path TEXT, is_calc INTEGER,"
        " pool TEXT, source_label TEXT, source_url TEXT, explanation_url TEXT);"
        "INSERT INTO questions VALUES('fe-01_aki-q01','01_aki','回',1,1,'本文','[{\"label\":\"ア\",\"text\":\"x\"}]',"
        "'ア',NULL,1,'study','s','u','e');")
    conn.commit()
    conn.close()
    out = tmp_path / "dist"
    pack.main(["--db", str(db), "--out", str(out), "--images", str(tmp_path)])
    _, questions, _, _, _ = _read_zip(out / "fe-pack-public.zip")
    q = questions[0]
    assert q["is_calc"] is True
    assert q["choices"] == [{"label": "ア", "text": "x"}]
    assert q["part"] == "A"
    assert q["explanation_images"] == []
    assert q["tags"] == []
    assert q["group_id"] is None and q["images"] is None
    assert not (out / "fe-pack-private.zip").exists()


# ---------- build_site ----------

STATIC_INDEX = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<link rel="manifest" href="static/manifest.webmanifest">
<link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<script>
/* theme */
</script>
<link rel="stylesheet" href="/static/app.css">
</head>
<body><main id="view"></main>
<script src="/static/app.js" defer></script>
</body>
</html>
"""


@pytest.fixture
def static_dir(tmp_path):
    s = tmp_path / "static"
    (s / "local").mkdir(parents=True)
    (s / "index.html").write_text(STATIC_INDEX, encoding="utf-8")
    (s / "app.js").write_text("// app", encoding="utf-8")
    (s / "app.css").write_text("body{}", encoding="utf-8")
    (s / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (s / "manifest.webmanifest").write_text(json.dumps({
        "name": "FE", "start_url": "/", "scope": "/",
        "icons": [{"src": "/static/icon.svg", "sizes": "any"}]}), encoding="utf-8")
    (s / "sw.js").write_text("const SHELL = ['/', '/static/app.js'];\n"
                             "self.addEventListener('fetch', (e) => cache.match('/'));\n"
                             "const API = '/api/x';\n", encoding="utf-8")
    for n in ["store.js", "logic.js", "pack.js", "backup.js", "api-local.js"]:
        (s / "local" / n).write_text("// " + n, encoding="utf-8")
    return s


def test_build_site_outputs(env, static_dir, tmp_path):
    pub, _ = _pack(env)
    out = tmp_path / "fe"
    build_site.build(pub, out, static_dir, log=lambda *_: None)

    html = (out / "index.html").read_text(encoding="utf-8")
    assert "window.FE_STANDALONE=true" in html
    assert html.index("FE_STANDALONE") < html.index("<script>\n/* theme */")
    assert 'href="/' not in html and 'src="/' not in html
    local_idx = [html.index(f"static/local/{n}") for n in
                 ["store.js", "logic.js", "pack.js", "backup.js", "api-local.js"]]
    assert local_idx == sorted(local_idx)
    assert local_idx[-1] < html.index("static/app.js")
    assert 'href="./manifest.webmanifest"' in html

    man = json.loads((out / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["start_url"] == "./" and man["scope"] == "./"
    assert man["icons"][0]["src"] == "./static/icon.svg"

    sw = (out / "sw.js").read_text(encoding="utf-8")
    assert "'/static/" not in sw and "cache.match('./')" in sw and "'/api/x'" in sw

    assert (out / ".nojekyll").exists()
    assert (out / "static" / "local" / "store.js").exists()
    idx = json.loads((out / "packs" / "index.json").read_text(encoding="utf-8"))
    assert idx["public"]["file"] == "fe-pack-public.zip"
    assert idx["public"]["size"] == (out / "packs" / "fe-pack-public.zip").stat().st_size
    assert idx["public"]["question_count"] == len(PUBLIC_IDS)
    assert idx["public"]["created_at"]


def test_build_site_rejects_private_pack(env, static_dir, tmp_path):
    _, priv = _pack(env)
    with pytest.raises(build_site.BuildError, match="private"):
        build_site.build(priv, tmp_path / "fe", static_dir, log=lambda *_: None)
    assert not (tmp_path / "fe").exists()


def test_build_site_rejects_forged_public_pack(env, static_dir, tmp_path):
    """kind を public と偽っても、furoku 行を含めば拒否する。"""
    _, priv = _pack(env)
    _, questions, _, imgs, _ = _read_zip(priv)
    forged = tmp_path / "forged.zip"
    manifest = {"format": "fe-pack", "version": 1, "kind": "public", "created_at": "x",
                "question_count": len(questions), "patch_count": 0, "image_count": 0}
    with zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("questions.json", json.dumps(questions))
    with pytest.raises(build_site.BuildError, match="furoku"):
        build_site.build(forged, tmp_path / "fe", static_dir, log=lambda *_: None)


def test_build_site_rejects_explanation_images_in_public(env, static_dir, tmp_path):
    pub, _ = _pack(env)
    manifest, questions, _, imgs, _ = _read_zip(pub)
    questions[0]["explanation_images"] = ["bx/x.png"]
    forged = tmp_path / "leak.zip"
    with zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("questions.json", json.dumps(questions))
    with pytest.raises(build_site.BuildError, match="解説画像"):
        build_site.build(forged, tmp_path / "fe", static_dir, log=lambda *_: None)


def test_build_site_rejects_private_file_left_in_out(env, static_dir, tmp_path):
    """生成物以外の場所に残った private zip は出力後の検査で拒否する。packs/ の古いものは掃除される。"""
    pub, _ = _pack(env)
    out = tmp_path / "fe"
    (out / "misc").mkdir(parents=True)
    (out / "misc" / "fe-pack-private.zip").write_bytes(b"x")
    with pytest.raises(build_site.BuildError, match="private"):
        build_site.build(pub, out, static_dir, log=lambda *_: None)


def test_build_site_cleans_stale_generated_packs(env, static_dir, tmp_path):
    pub, _ = _pack(env)
    out = tmp_path / "fe"
    (out / "packs").mkdir(parents=True)
    (out / "packs" / "fe-pack-private.zip").write_bytes(b"stale")
    build_site.build(pub, out, static_dir, log=lambda *_: None)
    assert not (out / "packs" / "fe-pack-private.zip").exists()
    assert (out / "packs" / "fe-pack-public.zip").exists()
