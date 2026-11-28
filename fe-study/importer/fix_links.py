"""explanation_url の整合を取る（冪等）。

- 許可リスト外の回の explanation_url を空にする
- 許可リスト内の回で explanation_url が空の科目A行には規則どおりの URL を設定する

    python -m importer.fix_links [--db data/fe.db]
"""
import argparse
import sqlite3
from pathlib import Path

from .ipa_fetch import VERIFIED_EXPLANATION_CODES

ROOT = Path(__file__).resolve().parent.parent


def fix(db: Path) -> dict:
    """戻り値: {'cleared': {exam_code: 件数}, 'filled': {exam_code: 件数}}"""
    conn = sqlite3.connect(db)
    ph = ",".join("?" * len(VERIFIED_EXPLANATION_CODES))
    args = sorted(VERIFIED_EXPLANATION_CODES)

    # 1) 許可リスト外の回のリンクを消す
    where_clear = f"part='A' AND explanation_url!='' AND exam_code NOT IN ({ph})"
    cleared = conn.execute(
        f"SELECT exam_code, COUNT(*) FROM questions WHERE {where_clear} GROUP BY exam_code", args).fetchall()
    conn.execute(f"UPDATE questions SET explanation_url='' WHERE {where_clear}", args)

    # 2) 許可リスト内の回で空のものに規則どおりの URL を付ける（2回目以降は対象0件）
    where_fill = f"part='A' AND explanation_url='' AND exam_code IN ({ph})"
    filled = conn.execute(
        f"SELECT exam_code, COUNT(*) FROM questions WHERE {where_fill} GROUP BY exam_code", args).fetchall()
    conn.execute(
        "UPDATE questions SET explanation_url="
        "'https://www.fe-siken.com/kakomon/' || exam_code || '/q' || number || '.html' "
        f"WHERE {where_fill}", args)

    conn.commit()
    conn.close()
    return {"cleared": dict(cleared), "filled": dict(filled)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "fe.db"))
    res = fix(Path(ap.parse_args().db))
    for kind in ("cleared", "filled"):
        for k, v in sorted(res[kind].items()):
            print(f"{kind} {k}: {v}")
    print(f"cleared {sum(res['cleared'].values())} rows, filled {sum(res['filled'].values())} rows")


if __name__ == "__main__":
    main()
