"""許可リスト外の回の explanation_url を空にする（冪等）。

    python -m importer.fix_links [--db data/fe.db]
"""
import argparse
import sqlite3
from pathlib import Path

from .ipa_fetch import VERIFIED_EXPLANATION_CODES

ROOT = Path(__file__).resolve().parent.parent


def fix(db: Path) -> dict:
    conn = sqlite3.connect(db)
    ph = ",".join("?" * len(VERIFIED_EXPLANATION_CODES))
    where = f"part='A' AND explanation_url!='' AND exam_code NOT IN ({ph})"
    args = sorted(VERIFIED_EXPLANATION_CODES)
    rows = conn.execute(f"SELECT exam_code, COUNT(*) FROM questions WHERE {where} GROUP BY exam_code", args).fetchall()
    conn.execute(f"UPDATE questions SET explanation_url='' WHERE {where}", args)
    conn.commit()
    conn.close()
    return dict(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "fe.db"))
    res = fix(Path(ap.parse_args().db))
    for k, v in sorted(res.items()):
        print(f"{k}: {v}")
    print(f"cleared {sum(res.values())} rows")


if __name__ == "__main__":
    main()
