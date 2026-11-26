"""開発確認用のデモ問題投入。`python -m app.demo_seed`。実データがあれば何もしない。"""
import json
import sys

from . import db
from .categories import CATEGORIES

CALC_CATS = {2: "2 の 10 乗はいくつか。", 3: "1KB=1024B のとき 2KB は何 B か。", 9: "3 + 4 × 2 はいくつか。"}
CALC_ANS = {2: ("1024", "512", "2048", "100"), 3: ("2048", "2000", "1024", "4096"), 9: ("11", "14", "9", "24")}
LABELS = "アイウエ"


def _choices(texts):
    return json.dumps([{"label": l, "text": t} for l, t in zip(LABELS, texts)], ensure_ascii=False)


def seed(path=None):
    path = db.resolve_db_path(path)
    db.init_db(path)
    conn = db.connect(path)
    try:
        if conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] > 0:
            print("questions が既にあるためスキップしました")
            return False
        rows = []
        for c in CATEGORIES:
            for i in (1, 2):
                ans = LABELS[(c["id"] + i) % 4]
                body = (f"【デモ】分野「{c['name']}」の動作確認用ダミー問題です。"
                        f"正解は「{ans}」です。（デモ{i}）")
                texts = [f"これは選択肢{l}" for l in LABELS]
                rows.append((f"fe-demo-q{c['id']:02d}{i}", "00_demo", "デモ", c["id"] * 10 + i, c["id"],
                             body, _choices(texts), ans, 0, "study"))
        for cid, text in CALC_CATS.items():
            texts = list(CALC_ANS[cid])
            rows.append((f"fe-demo-q{cid:02d}9", "00_demo", "デモ", cid * 10 + 9, cid,
                         f"【デモ】計算問題: {text}", _choices(texts), "ア", 1, "study"))
        for n in range(1, 6):
            ans = LABELS[n % 4]
            rows.append((f"fe-demo-mock-q{n:02d}", "05_demo", "デモ模試", n, CATEGORIES[(n * 4) % 23]["id"],
                         f"【デモ】模擬試験用ダミー問題 {n}。正解は「{ans}」です。",
                         _choices([f"これは選択肢{l}" for l in LABELS]), ans, 0, "mock"))
        for r in rows:
            conn.execute(
                "INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,"
                "is_calc,pool,source_label,source_url,explanation_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                r + ("【デモ】自作ダミー問題", "https://example.invalid/demo", "https://example.invalid/demo"))
        conn.commit()
        print(f"デモ問題 {len(rows)} 問を投入しました: {path}")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
    sys.exit(0)
