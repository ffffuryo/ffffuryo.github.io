import json
import sqlite3

from conftest import answer_all, start

from app.db import init_db


def sess_b(client, **kw):
    return start(client, part="B", **{"mode": "study", **kw})


def group_ids(s):
    return [q["group_id"] or q["id"] for q in s["questions"]]


# ---- migration ----
OLD_SCHEMA = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, field TEXT NOT NULL, major_no INTEGER NOT NULL,
  major_name TEXT NOT NULL, name TEXT NOT NULL);
CREATE TABLE questions (id TEXT PRIMARY KEY, exam_code TEXT NOT NULL, exam_label TEXT NOT NULL,
  number INTEGER NOT NULL, category_id INTEGER NOT NULL, body TEXT NOT NULL, choices TEXT NOT NULL,
  answer TEXT NOT NULL, image_path TEXT, is_calc INTEGER NOT NULL DEFAULT 0, pool TEXT NOT NULL,
  source_label TEXT NOT NULL, source_url TEXT NOT NULL, explanation_url TEXT NOT NULL,
  parse_confidence REAL NOT NULL DEFAULT 1.0, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
"""


def test_migration_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,pool,"
              "source_label,source_url,explanation_url) VALUES('old','30_aki','x',1,1,'b','[]','ア','study','s','u','e')")
    c.commit()
    c.close()
    for _ in range(2):
        init_db(path)  # 2回目もエラーなし
    c = sqlite3.connect(path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(questions)")}
    assert {"part", "images", "group_id", "sub_label", "b_topic", "tags", "difficulty",
            "explanation_images", "explanation_source"} <= cols
    assert c.execute("SELECT part FROM questions WHERE id='old'").fetchone()[0] == "A"
    idx = {r[1] for r in c.execute("PRAGMA index_list(questions)")}
    assert "idx_q_part" in idx
    assert c.execute("SELECT value FROM settings WHERE key='session_size_b'").fetchone()[0] == "2"
    c.close()


def test_migration_keeps_existing_settings(tmp_path):
    path = str(tmp_path / "s.db")
    init_db(path)
    c = sqlite3.connect(path)
    c.execute("UPDATE settings SET value='3' WHERE key='session_size_b'")
    c.commit()
    c.close()
    init_db(path)
    c = sqlite3.connect(path)
    assert c.execute("SELECT value FROM settings WHERE key='session_size_b'").fetchone()[0] == "3"


# ---- settings ----
def test_settings_b(client):
    s = client.get("/api/settings").get_json()
    assert s["session_size_b"] == 2 and s["target_sec_b"] == 300 and s["b_topics"] == []
    r = client.put("/api/settings", json={"session_size_b": 4, "b_topics": ["security"], "target_sec_b": 240})
    assert r.get_json()["b_topics"] == ["security"] and r.get_json()["session_size_b"] == 4
    for bad in ({"session_size_b": 0}, {"session_size_b": 5}, {"b_topics": ["x"]}, {"b_topics": "pseudo"},
                {"target_sec_b": 5}, {"session_size_b": True}):
        assert client.put("/api/settings", json=bad).status_code == 400, bad


# ---- sessions ----
def test_part_validation(client):
    assert client.post("/api/sessions", json={"mode": "study", "part": "C"}).status_code == 400
    assert client.post("/api/sessions", json={"mode": "study", "part": None}).status_code == 400
    assert client.post("/api/sessions", json={"mode": "study", "part": "B", "b_topics": ["zzz"]}).status_code == 400


def test_study_b_size_is_groups_and_contiguous(client):
    for size in (1, 2, 3, 6, 10):
        s = sess_b(client, size=size)
        gids = group_ids(s)
        assert len(set(gids)) == min(size, 6)
        # 同一グループは連続
        seen, prev = set(), None
        for g in gids:
            if g != prev:
                assert g not in seen
                seen.add(g)
            prev = g
        assert s["part"] == "B" and s["mode"] == "study"
        for q in s["questions"]:
            assert q["part"] == "B"
        if "furoku3-q01" in gids:
            subs = [q["sub_label"] for q in s["questions"] if q["group_id"] == "furoku3-q01"]
            assert subs == ["設問1", "設問2", "設問3(2)"]


def test_study_b_default_size_from_settings(client):
    assert len(set(group_ids(sess_b(client)))) == 2
    client.put("/api/settings", json={"session_size_b": 4})
    assert len(set(group_ids(sess_b(client)))) == 4


def test_study_b_object_fields(client):
    s = sess_b(client, size=6)
    q = next(x for x in s["questions"] if x["id"] == "feb-sample-q01")
    assert q["image_urls"] == ["/media/b/feb-sample-q01-1.png", "/media/b/feb-sample-q01-2.png"]
    assert q["b_topic"] == "pseudo" and q["tags"] == ["トレース", "再帰"] and q["difficulty"] == "やさしい"
    assert q["has_book_explanation"] is True and q["sub_label"] is None and q["group_id"] is None
    assert len(q["choices"]) == 10 and q["choices"][-1]["label"] == "コ"
    q3 = next(x for x in s["questions"] if x["id"] == "feb-sample-q03")
    assert q3["has_book_explanation"] is False
    assert q["explanation_url"] == ""


def test_b_never_mock_and_a_never_b(client):
    for _ in range(5):
        s = sess_b(client, size=10)
        assert all(not q["id"].startswith("feb-05") for q in s["questions"])
        a = start(client, mode="study", size=15)
        assert all(q["part"] == "A" and not q["id"].startswith("feb-") for q in a["questions"])
    # A の模試にも B は混ざらない
    m = start(client, mode="mock", exam_code="05_haru")
    assert len(m["questions"]) == 10 and all(q["part"] == "A" for q in m["questions"])
    m = start(client, mode="mock")
    assert all(q["part"] == "A" for q in m["questions"])


def test_b_topics_filter(client):
    s = sess_b(client, size=10, b_topics=["pseudo"])
    assert {q["b_topic"] for q in s["questions"]} == {"pseudo"} and len(s["questions"]) == 3
    client.put("/api/settings", json={"b_topics": ["security"]})
    s = sess_b(client, size=10)
    assert {q["b_topic"] for q in s["questions"]} == {"security"}
    assert len(s["questions"]) == 5 and len(set(group_ids(s))) == 3
    s = sess_b(client, size=10, b_topics=["pseudo", "security"])
    assert len(s["questions"]) == 8


def test_study_b_answer_reveals_explanation(client):
    s = sess_b(client, size=6)
    q = next(x for x in s["questions"] if x["id"] == "feb-sample-q02")  # 正解 コ
    r = client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "コ", "time_ms": 100000}).get_json()
    assert r["is_correct"] is True and r["answer"] == "コ"
    assert r["explanation_image_urls"] == ["/media/bx/feb-sample-q02-e1.png"]
    assert r["explanation_source"] == "【テスト】解説出典"
    cur = client.get(f"/api/sessions/{s['id']}").get_json()
    att = next(x for x in cur["questions"] if x["id"] == q["id"])["attempt"]
    assert att["explanation_image_urls"] == ["/media/bx/feb-sample-q02-e1.png"] and att["answer"] == "コ"
    # 未回答の問題には出ない
    other = next(x for x in cur["questions"] if x["id"] != q["id"])
    assert other["attempt"] is None
    # B の選択肢は ア〜コ、A は ア〜エ のみ
    bad = client.post(f"/api/sessions/{s['id']}/answer",
                      json={"question_id": other["id"], "chosen": "ン", "time_ms": 1})
    assert bad.status_code == 400
    a = start(client, mode="study", size=5)
    assert client.post(f"/api/sessions/{a['id']}/answer", json={
        "question_id": a["questions"][0]["id"], "chosen": "オ", "time_ms": 1}).status_code == 400


def test_mock_b(client):
    s = start(client, mode="mock", part="B", exam_code="05_haru")
    assert [q["id"] for q in s["questions"]] == [f"feb-05_haru-q{n:02d}" for n in (1, 2, 3)]
    assert s["time_limit_sec"] == 900 and s["part"] == "B"
    q = s["questions"][0]
    r = client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "ア", "time_ms": 3000}).get_json()
    assert r == {"saved": True}
    cur = client.get(f"/api/sessions/{s['id']}").get_json()
    assert cur["questions"][0]["attempt"] == {"chosen": "ア", "time_ms": 3000}
    assert client.get(f"/api/questions/{q['id']}").get_json()["answer"] is None
    assert "explanation_image_urls" not in client.get(f"/api/questions/{q['id']}").get_json()
    f = client.post(f"/api/sessions/{s['id']}/finish").get_json()
    assert f["total"] == 3 and f["correct"] == 1 and f["passed"] is False
    a0 = f["questions"][0]["attempt"]
    assert a0["answer"] == "ア" and a0["explanation_image_urls"] == ["/media/bx/feb-05_haru-q01-e1.png"]
    assert f["questions"][1]["attempt"] is None
    d = client.get(f"/api/questions/{q['id']}").get_json()
    assert d["answer"] == "ア" and d["explanation_image_urls"] and d["part"] == "B"


def test_mock_b_all_and_review_b(client):
    s = start(client, mode="mock", part="B")
    assert len(s["questions"]) == 3 and s["time_limit_sec"] == 900
    assert client.post("/api/sessions", json={"mode": "mock", "part": "B", "exam_code": "zz"}).status_code == 409
    # 復習 B
    assert client.post("/api/sessions", json={"mode": "review", "part": "B"}).status_code == 409
    st = sess_b(client, size=6)
    for q in st["questions"]:
        ch = "イ" if q["id"] in ("feb-sample-q01", "feb-furoku3-q01-2") else "ア"
        if q["id"] == "feb-sample-q02":
            ch = "コ"
        client.post(f"/api/sessions/{st['id']}/answer", json={"question_id": q["id"], "chosen": ch, "time_ms": 1000})
    client.post(f"/api/sessions/{st['id']}/finish")
    h = client.get("/api/home").get_json()
    assert h["pool_b"]["review"] == 2 and h["pool"]["review"] == 0
    r = start(client, mode="review", part="B")
    ids = {q["id"] for q in r["questions"]}
    assert ids == {"feb-sample-q01", "feb-furoku3-q01-2"}
    # A の復習には B が出ない
    assert client.post("/api/sessions", json={"mode": "review"}).status_code == 409


def test_unseen_and_wrong_priority_b(client):
    st = sess_b(client, size=6)
    first = st["questions"][0]
    client.post(f"/api/sessions/{st['id']}/answer", json={"question_id": first["id"], "chosen": "イ", "time_ms": 1})
    # 同日の誤答は後回しだが、枠が余れば入る。size=6 で全グループが入る
    s2 = sess_b(client, size=6)
    assert len(set(group_ids(s2))) == 6
    s3 = sess_b(client, size=2)
    assert len(set(group_ids(s3))) == 2


# ---- home / history / stats ----
def test_home_b(client):
    h = client.get("/api/home").get_json()
    assert h["pool_b"] == {"study": 8, "study_unseen": 8, "mock": 3, "review": 0}
    assert h["pool"] == {"study": 69, "study_unseen": 69, "mock": 10, "review": 0}
    ex = {(e["part"], e["exam_code"]): e["count"] for e in h["mock_exams"]}
    assert ex == {("A", "05_haru"): 10, ("B", "05_haru"): 3}
    s = sess_b(client)
    a = client.get("/api/home").get_json()["active_session"]
    assert a["part"] == "B" and a["total"] == len(s["questions"])


def test_stats_categories_part(client):
    s = sess_b(client, size=6)
    answer_all(client, s)  # 全部 ア（q02 のみ不正解）
    a = client.get("/api/stats/categories").get_json()
    assert sum(c["seen"] for f in a["fields"] for c in f["categories"]) == 0
    b = client.get("/api/stats/categories?part=B").get_json()
    cats = {c["id"]: c for f in b["fields"] for c in f["categories"]}
    assert cats[2]["total"] == 3 and cats[11]["total"] == 5 and cats[2]["seen"] == 3
    assert client.get("/api/stats/categories?part=Z").status_code == 400


def test_stats_b(client):
    s = sess_b(client, size=6)
    answer_all(client, s, time_ms=200000)
    d = client.get("/api/stats/b").get_json()
    topics = {t["b_topic"]: t for t in d["topics"]}
    assert topics["pseudo"]["total"] == 3 and topics["pseudo"]["seen"] == 3
    assert topics["pseudo"]["accuracy"] == round(2 / 3, 4)  # q02 は正解 コ なので ア は不正解
    assert topics["security"]["total"] == 5 and topics["security"]["accuracy"] == 1.0
    assert topics["security"]["avg_time_ms"] == 200000 and topics["pseudo"]["name"] == "擬似言語"
    tags = {t["tag"]: t for t in d["tags"]}
    assert tags["トレース"]["total"] == 2 and tags["トレース"]["accuracy"] == 0.5
    assert tags["認証"]["total"] == 4 and tags["暗号"]["total"] == 2 and tags["再帰"]["total"] == 1
    diff = {t["difficulty"]: t for t in d["difficulty"]}
    assert list(diff) == ["やさしい", "ふつう", "難しい"]
    assert diff["難しい"]["total"] == 4 and diff["やさしい"]["accuracy"] == 1.0


def test_stats_b_empty(client):
    d = client.get("/api/stats/b").get_json()
    assert [t["accuracy"] for t in d["topics"]] == [None, None]
    assert all(t["seen"] == 0 for t in d["tags"])


def test_stats_time_part(client):
    answer_all(client, sess_b(client, size=2), time_ms=250000)
    answer_all(client, start(client, mode="study", size=5), time_ms=10000)
    n = lambda p: sum(x["n"] for x in client.get(f"/api/stats/time?part={p}").get_json()["by_category"])
    nall = sum(x["n"] for x in client.get("/api/stats/time").get_json()["by_category"])
    assert n("A") == 5 and n("B") >= 2 and nall == n("A") + n("B") == n("all")
    t = client.get("/api/stats/time?part=B").get_json()
    assert t["overall"]["avg_ms"] == 250000 and t["overall"]["over_target_rate"] == 0.0
    assert client.get("/api/stats/time?part=X").status_code == 400


def test_history_and_question_b(client):
    s = sess_b(client, size=1)
    answer_all(client, s)
    h = client.get("/api/history").get_json()
    assert len(h) == len(s["questions"]) and all(x["part"] == "B" for x in h)
    q0 = s["questions"][0]
    d = client.get(f"/api/questions/{q0['id']}").get_json()
    assert d["part"] == "B" and d["image_urls"] and d["answer"] == "ア"
    assert d["tags"] == q0["tags"] and "has_book_explanation" in d
    assert d["explanation_image_urls"] == (["/media/bx/%s-e1.png" % q0["id"]] if q0["has_book_explanation"] else [])
    a = start(client, mode="study", size=5)
    answer_all(client, a)
    assert client.get("/api/history?limit=1").get_json()[0]["part"] == "A"
