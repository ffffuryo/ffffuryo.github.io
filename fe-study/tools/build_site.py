"""GitHub Pages 用のスタンドアロン版サイト `fe/` を生成する（SPEC 7.6）。

    cd fe-study && python -m tools.build_site [--pack dist/fe-pack-public.zip] [--out ../fe]

- static/ を fe/static/ にコピーし、index.html / sw.js / manifest を相対パス用に書き換えて生成する。
- public パックを fe/packs/ に同梱し、fe/packs/index.json を書く。
- private パック（書籍付録由来）は絶対に fe/ に入れない。pack の検査と、出力後の fe/ 全体の検査で拒否する。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PACK = BASE_DIR / "dist" / "fe-pack-public.zip"
DEFAULT_OUT = BASE_DIR.parent / "fe"
DEFAULT_STATIC = BASE_DIR / "static"

LOCAL_SCRIPTS = ["store.js", "logic.js", "pack.js", "backup.js", "api-local.js"]
# fe/ 直下・fe/static 内のうち、本スクリプトが再生成する対象（掃除してから作り直す）
GENERATED = ["static", "packs", "index.html", "sw.js", "manifest.webmanifest", ".nojekyll"]


class BuildError(Exception):
    pass


# ---------- 検査（書籍付録由来が混入していないか） ----------

def _check_questions(questions: list[dict], where: str) -> None:
    for q in questions:
        if str(q.get("exam_code") or "").startswith("furoku"):
            raise BuildError(f"{where}: furoku 由来の問題が含まれています（id={q.get('id')}）")
        if q.get("explanation_images"):
            raise BuildError(f"{where}: 解説画像（書籍由来）を含む行があります（id={q.get('id')}）")


def check_public_pack(pack: Path) -> dict:
    """public パックとして安全か検査し、manifest を返す。違反は BuildError。"""
    if not pack.is_file():
        raise BuildError(f"パックが見つかりません: {pack}")
    try:
        z = zipfile.ZipFile(pack)
    except zipfile.BadZipFile as e:
        raise BuildError(f"zip として読めません: {pack} ({e})") from e
    with z:
        names = set(z.namelist())
        if "patches.json" in names:
            raise BuildError(f"{pack.name}: patches.json を含むため public ではありません")
        try:
            manifest = json.loads(z.read("manifest.json"))
            questions = json.loads(z.read("questions.json"))
        except KeyError as e:
            raise BuildError(f"{pack.name}: 必要なファイルがありません: {e}") from e
    if manifest.get("kind") != "public":
        raise BuildError(f"{pack.name}: manifest.kind が public ではありません（{manifest.get('kind')!r}）。"
                         "private パックは fe/ に入れられません")
    _check_questions(questions, pack.name)
    return manifest


def check_fe_tree(out: Path) -> None:
    """出力先 fe/ 全体を検査する。private 由来のファイルや、他の不正な zip があれば BuildError。"""
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(out).as_posix()
        if "private" in f.name.lower():
            raise BuildError(f"fe/ に private 由来のファイルがあります: {rel}")
        if f.name == "patches.json":
            raise BuildError(f"fe/ に patches.json があります: {rel}")
        if f.suffix.lower() == ".zip":
            check_public_pack(f)
    pub = out / "packs" / "fe-pack-public.zip"
    if not pub.is_file():
        raise BuildError("fe/packs/fe-pack-public.zip が生成されていません")


# ---------- 書き換え ----------

def make_index(html: str) -> str:
    """static/index.html を fe/ 用に変換する。"""
    # manifest は fe/manifest.webmanifest に置く
    html = re.sub(r'(href=")(?:\./|/)?static/manifest\.webmanifest(")', r'\1./manifest.webmanifest\2', html)
    # 絶対パス（/...）を相対（./...）に。プロトコル相対（//...）は触らない
    html = re.sub(r'((?:href|src)=")/(?!/)', r'\1./', html)

    # <head> 内の最初の <script の前に FE_STANDALONE を入れる（無ければ </head> の前）
    lower = html.lower()
    head_open = re.search(r"<head[^>]*>", lower)
    head_close = lower.find("</head>")
    if not head_open or head_close < 0:
        raise BuildError("index.html に <head> / </head> がありません")
    pos = lower.find("<script", head_open.end(), head_close)
    if pos < 0:
        pos = head_close
    html = html[:pos] + "<script>window.FE_STANDALONE=true;</script>\n" + html[pos:]

    # app.js を読む <script> の直前に local/*.js を挿入（存在するものだけ。呼び出し側で決める）
    return html


def insert_local_scripts(html: str, present: list[str]) -> str:
    m = re.search(r'<script\b[^>]*\bsrc="(?:\./)?static/app\.js"[^>]*>\s*</script>', html)
    if not m:
        raise BuildError('index.html に static/app.js を読む <script> が見つかりません')
    tags = "".join(f'<script src="static/local/{n}" defer></script>\n' for n in present)
    return html[:m.start()] + tags + html[m.start():]


def make_sw(src: str) -> str:
    """sw.js の絶対パスを ./ 相対に（サブパス /fe/ で動くように）。"""
    src = src.replace("'/static/", "'./static/")
    src = src.replace("'/'", "'./'")
    return src


def make_manifest(src: str) -> str:
    m = json.loads(src)
    m["start_url"] = "./"
    m["scope"] = "./"
    for icon in m.get("icons", []):
        icon["src"] = "./" + icon["src"].lstrip("/")
    return json.dumps(m, ensure_ascii=False, indent=2) + "\n"


# ---------- 生成 ----------

def build(pack: Path, out: Path, static_src: Path = DEFAULT_STATIC, log=print) -> dict:
    pack = Path(pack).resolve()
    out = Path(out).resolve()
    static_src = Path(static_src).resolve()

    # 1. 入力パックを先に検査（不正なら何も書かない）
    manifest = check_public_pack(pack)
    if not (static_src / "index.html").is_file():
        raise BuildError(f"static/index.html がありません: {static_src}")

    # 2. 生成物だけ掃除して作り直す（out 直下のそれ以外には触らない）
    out.mkdir(parents=True, exist_ok=True)
    for name in GENERATED:
        p = out / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()

    # 3. static/ をコピー（local/ を含む）
    shutil.copytree(static_src, out / "static")
    present = [n for n in LOCAL_SCRIPTS if (static_src / "local" / n).is_file()]
    log(f"local スクリプト: {', '.join(present) if present else 'なし'}")

    # 4. index.html / sw.js / manifest
    index_html = insert_local_scripts(make_index((static_src / "index.html").read_text(encoding="utf-8")), present)
    (out / "index.html").write_text(index_html, encoding="utf-8")
    (out / "sw.js").write_text(make_sw((static_src / "sw.js").read_text(encoding="utf-8")), encoding="utf-8")
    manifest_txt = make_manifest((static_src / "manifest.webmanifest").read_text(encoding="utf-8"))
    (out / "manifest.webmanifest").write_text(manifest_txt, encoding="utf-8")
    (out / "static" / "manifest.webmanifest").write_text(manifest_txt, encoding="utf-8")

    # 5. public パックと index.json
    packs = out / "packs"
    packs.mkdir()
    dst = packs / "fe-pack-public.zip"
    shutil.copy2(pack, dst)
    index = {"public": {
        "file": dst.name,
        "size": dst.stat().st_size,
        "created_at": manifest.get("created_at"),
        "question_count": manifest.get("question_count"),
    }}
    (packs / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 6. GitHub Pages の Jekyll 無効化
    (out / ".nojekyll").write_text("", encoding="utf-8")

    # 7. 出力全体の最終検査
    check_fe_tree(out)
    log(f"生成完了: {out}（問題 {manifest.get('question_count')} 件 / "
        f"パック {dst.stat().st_size / 1024 / 1024:.2f} MB）")
    return index["public"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GitHub Pages 用のスタンドアロン版 fe/ を生成する（SPEC 7.6）")
    ap.add_argument("--pack", default=str(DEFAULT_PACK), help="public パック（既定: dist/fe-pack-public.zip）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="出力先（既定: リポジトリ直下の fe/）")
    ap.add_argument("--static", default=str(DEFAULT_STATIC), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        build(Path(args.pack), Path(args.out), Path(args.static))
    except BuildError as e:
        print(f"中止: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
