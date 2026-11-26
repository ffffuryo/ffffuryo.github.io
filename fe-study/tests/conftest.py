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
