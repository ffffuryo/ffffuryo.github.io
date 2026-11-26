import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.db import connect  # noqa: E402

CALC = {(2, 3), (3, 3), (9, 3)}  # (category, index) が計算問題


def _ins(conn, qid, exam, label, num, cat, calc, pool):
    conn.execute(
        "INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,image_path,"
        "is_calc,pool,source_label,source_url,explanation_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, exam, label, num, cat, f"本文 {qid}",
         json.dumps([{"label": l, "text": f"選択肢{l}"} for l in "アイウエ"], ensure_ascii=False),
         "ア", "x/q.png" if num == 1 else None, calc, pool, "出典", "http://src", f"http://exp/{qid}"))


B_LABELS = "アイウエオカキクケコ"


def _ins_b(conn, qid, exam, label, num, topic, pool, *, group=None, sub=None, tags=(), diff=None,
           answer="ア", expl=True):
    """科目B のテスト用ダミー問題（自作の内容のみ）。画像は存在しないパスでよい。"""
    conn.execute(
        "INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,pool,"
        "source_label,source_url,explanation_url,part,images,group_id,sub_label,b_topic,tags,difficulty,"
        "explanation_images,explanation_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, exam, label, num, 2 if topic == "pseudo" else 11, f"【テスト】B問題 {qid}",
         json.dumps([{"label": l, "text": "" if i > 3 else f"択{l}"} for i, l in enumerate(B_LABELS)],
                    ensure_ascii=False),
         answer, pool, "【テスト】出典", "http://src", "", "B",
         json.dumps([f"b/{qid}-1.png", f"b/{qid}-2.png"]), group, sub, topic,
         json.dumps(list(tags), ensure_ascii=False) if tags else None, diff,
         json.dumps([f"bx/{qid}-e1.png"]) if expl else None,
         "【テスト】解説出典" if expl else None))


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path / "t.db"))
    a.config["RNG"] = random.Random(1234)
    a.config["IMAGES_DIR"] = str(tmp_path / "images")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "ok.png").write_bytes(b"PNG")
    (tmp_path / "secret.txt").write_text("secret")
    conn = connect(a.config["DB_PATH"])
    for cid in range(1, 24):
        for i in range(1, 4):
            _ins(conn, f"fe-30_aki-q{cid:02d}{i}", "30_aki", "平成30年秋期", cid * 10 + i, cid,
                 int((cid, i) in CALC), "study")
    for n in range(1, 11):
        _ins(conn, f"fe-05_haru-q{n:02d}", "05_haru", "令和5年春期", n, (n - 1) % 23 + 1, 0, "mock")
    # 科目B: study 8問 = 6グループ（うち1グループは3設問）
    _ins_b(conn, "feb-sample-q01", "sample", "サンプル", 1, "pseudo", "study",
           tags=["トレース", "再帰"], diff="やさしい")
    _ins_b(conn, "feb-sample-q02", "sample", "サンプル", 2, "pseudo", "study",
           tags=["トレース"], diff="ふつう", answer="コ")
    _ins_b(conn, "feb-sample-q03", "sample", "サンプル", 3, "pseudo", "study",
           tags=["一次元配列"], diff="難しい", expl=False)
    _ins_b(conn, "feb-sample-q04", "sample", "サンプル", 4, "security", "study", tags=["暗号"], diff="ふつう")
    _ins_b(conn, "feb-sample-q05", "sample", "サンプル", 5, "security", "study", tags=["暗号", "認証"])
    for i, sub in ((3, "設問3(2)"), (1, "設問1"), (2, "設問2")):  # 挿入順をわざと崩す
        _ins_b(conn, f"feb-furoku3-q01-{i}", "furoku3", "付録3", 1, "security", "study",
               group="furoku3-q01", sub=sub, tags=["認証"], diff="難しい")
    for n in range(1, 4):
        _ins_b(conn, f"feb-05_haru-q{n:02d}", "05_haru", "令和5年", n, "pseudo", "mock", tags=["トレース"])
    conn.commit()
    conn.close()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    c = connect(app.config["DB_PATH"])
    yield c
    c.close()


def start(client, **body):
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def answer_all(client, sess, chosen="ア", time_ms=10000):
    for q in sess["questions"]:
        r = client.post(f"/api/sessions/{sess['id']}/answer",
                        json={"question_id": q["id"], "chosen": chosen, "time_ms": time_ms})
        assert r.status_code == 200
