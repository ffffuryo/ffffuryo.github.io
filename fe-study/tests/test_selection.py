import random

from app import selection
from conftest import answer_all, start


def test_study_never_mock(client, conn):
    for seed in range(20):
        ids = selection.pick_study(conn, 15, True, [], random.Random(seed))
        assert len(ids) == 15 and len(set(ids)) == 15
        assert not any(i.startswith("fe-05_haru") for i in ids)
    s = start(client, mode="study", size=15)
    assert all(not q["id"].startswith("fe-05_haru") for q in s["questions"])
    # 全問引いても mock は入らない
    assert len(selection.pick_study(conn, 100, True, [], random.Random(0))) == 69


def test_include_calc_false(client, conn):
    client.put("/api/settings", json={"include_calc": False})
    for _ in range(10):
        s = start(client, mode="study", size=15)
        assert not any(q["is_calc"] for q in s["questions"])
    assert len(selection.pick_study(conn, 100, False, [], random.Random(0))) == 66


def test_category_filter(conn):
    ids = selection.pick_study(conn, 15, True, [4, 7], random.Random(0))
    assert len(ids) == 6
    cats = {r[0] for r in conn.execute(
        f"SELECT category_id FROM questions WHERE id IN ({','.join('?' * len(ids))})", ids)}
    assert cats == {4, 7}


def test_deterministic(conn):
    a = selection.pick_study(conn, 8, True, [], random.Random(7))
    b = selection.pick_study(conn, 8, True, [], random.Random(7))
    assert a == b


def test_priority_wrong_slow_mastered(client, conn):
    # 3問ずつ: 不正解 / 遅い正解 / 速い正解 を作る
    qs = [r[0] for r in conn.execute("SELECT id FROM questions WHERE pool='study' ORDER BY id LIMIT 6")]
    sid = conn.execute("INSERT INTO sessions(mode,question_ids) VALUES('study','[]')").lastrowid
    plan = {qs[0]: ("イ", 0, 1000), qs[1]: ("ア", 1, 200000), qs[2]: ("ア", 1, 1000)}
    for q, (ch, ok, t) in plan.items():
        conn.execute("INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                     "VALUES(?,?,?,?,?,'2020-01-01 10:00:00')", (sid, q, ch, ok, t))
    conn.commit()
    ids = selection.pick_study(conn, 8, True, [], random.Random(3))
    assert qs[0] in ids and qs[1] in ids and qs[2] in ids  # 枠: wrong2/unseen4/slow1/mastered1 → 各枠に入る
    ids = selection.pick_study(conn, 69, True, [], random.Random(3))
    assert len(ids) == 69


def test_wrong_same_day_later(conn):
    qs = [r[0] for r in conn.execute("SELECT id FROM questions WHERE pool='study' ORDER BY id LIMIT 4")]
    sid = conn.execute("INSERT INTO sessions(mode,question_ids) VALUES('study','[]')").lastrowid
    conn.execute("INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                 "VALUES(?,?,'イ',0,1000,'2020-01-01 10:00:00')", (sid, qs[0]))
    conn.execute("INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms) "
                 "VALUES(?,?,'イ',0,1000)", (sid, qs[1]))  # 今日
    conn.commit()
    for seed in range(10):
        ids = selection.pick_study(conn, 5, True, [], random.Random(seed))  # wrong枠=2(0.3*5=1.5→2)
        assert qs[0] in ids and qs[1] in ids  # 両方枠内(2)
        ids = selection.pick_study(conn, 3, True, [], random.Random(seed))  # wrong枠=1 → 昨日以前が優先
        assert qs[0] in ids


def test_pick_mock(conn):
    ids = selection.pick_mock(conn, "05_haru")
    assert ids == [f"fe-05_haru-q{n:02d}" for n in range(1, 11)]
    assert len(selection.pick_mock(conn, None, 4, random.Random(1))) == 4
    assert selection.pick_mock(conn, "99_x") == []
