from conftest import start


def _answer(conn, qid, chosen, ok, t, at, mode="study"):
    sid = conn.execute("INSERT INTO sessions(mode,question_ids,status) VALUES(?, '[]', 'done')", (mode,)).lastrowid
    conn.execute("INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                 "VALUES(?,?,?,?,?,?)", (sid, qid, chosen, ok, t, at))


def test_category_stats(client, conn):
    # 分野1: q1 不正解→正解(速い) / q2 正解(遅い) / q3 未回答。分野2: q 不正解
    _answer(conn, "fe-30_aki-q011", "イ", 0, 40000, "2020-01-01 10:00:00")
    _answer(conn, "fe-30_aki-q011", "ア", 1, 20000, "2020-01-02 10:00:00")
    _answer(conn, "fe-30_aki-q012", "ア", 1, 100000, "2020-01-02 10:00:00")
    _answer(conn, "fe-30_aki-q021", "イ", 0, 60000, "2020-01-02 10:00:00")
    conn.commit()
    d = client.get("/api/stats/categories").get_json()
    assert [f["field"] for f in d["fields"]] == ["te", "ma", "st"]
    assert sum(len(f["categories"]) for f in d["fields"]) == 23
    c1 = d["fields"][0]["categories"][0]
    assert (c1["total"], c1["seen"], c1["attempts"], c1["correct"]) == (3, 2, 3, 2)
    assert c1["accuracy"] == 1.0 and c1["mastered"] == 1
    assert c1["avg_time_ms"] == (40000 + 20000 + 100000) // 3
    c2 = d["fields"][0]["categories"][1]
    assert c2["accuracy"] == 0.0 and c2["seen"] == 1
    assert d["fields"][0]["categories"][2]["accuracy"] is None
    h = client.get("/api/home").get_json()
    assert h["weak_categories"][0] == {"id": 2, "name": "アルゴリズムとプログラミング", "accuracy": 0.0}


def test_time_stats(client, conn):
    _answer(conn, "fe-30_aki-q011", "ア", 1, 20000, "2020-01-02 10:00:00")
    _answer(conn, "fe-30_aki-q021", "イ", 0, 100000, "2020-01-02 10:00:00")
    _answer(conn, "fe-30_aki-q023", "ア", 1, 140000, "2020-01-02 10:00:00")  # 計算? cat2 idx3 = calc
    _answer(conn, "fe-30_aki-q012", "ア", 1, 50000, "2020-01-02 10:00:00")
    conn.execute("UPDATE attempts SET answered_at=datetime('now','localtime') WHERE time_ms=50000")
    conn.execute("UPDATE attempts SET answered_at=datetime('now','localtime','-40 days') WHERE time_ms=20000")
    conn.commit()
    d = client.get("/api/stats/time").get_json()
    assert d["overall"] == {"avg_ms": 77500, "median_ms": 75000, "over_target_rate": 0.5}
    assert d["calc_vs_noncalc"]["calc"] == {"avg_ms": 140000, "accuracy": 1.0, "n": 1}
    assert d["calc_vs_noncalc"]["noncalc"]["n"] == 3
    assert [x["time_ms"] for x in d["slowest"]] == [140000, 100000, 50000, 20000]
    assert d["slowest"][0]["label"] == "平成30年秋期 問23"
    sp = {b["bucket"]: (b["n"], b["accuracy"]) for b in d["speed_accuracy"]}
    assert sp == {"〜30秒": (1, 1.0), "30〜60秒": (1, 1.0), "60〜90秒": (0, None),
                  "90〜120秒": (1, 0.0), "120秒〜": (1, 1.0)}
    assert len(d["daily"]) == 1 and d["daily"][0]["n"] == 1 and d["daily"][0]["accuracy"] == 1.0
    cats = {c["id"]: c for c in d["by_category"]}
    assert cats[2]["n"] == 2 and cats[2]["avg_ms"] == 120000 and cats[2]["median_ms"] == 120000


def test_streak(client, conn):
    _answer(conn, "fe-30_aki-q011", "ア", 1, 1000, "2020-01-02 10:00:00")
    for off in (1, 2, 3, 5):  # 昨日から3日連続 + 飛んで5日前
        sid = conn.execute("INSERT INTO sessions(mode,question_ids,status) VALUES('study','[]','done')").lastrowid
        conn.execute("INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                     "VALUES(?,?,'ア',1,1000,datetime('now','localtime',?))",
                     (sid, f"fe-30_aki-q02{off % 3 + 1}", f"-{off} days"))
    conn.commit()
    assert client.get("/api/home").get_json()["streak_days"] == 3
    s = start(client, mode="study", size=5)
    client.post(f"/api/sessions/{s['id']}/answer",
                json={"question_id": s["questions"][0]["id"], "chosen": "ア", "time_ms": 1})
    assert client.get("/api/home").get_json()["streak_days"] == 4


def test_empty_stats(client):
    d = client.get("/api/stats/time").get_json()
    assert d["overall"]["avg_ms"] is None and d["slowest"] == [] and len(d["speed_accuracy"]) == 5
