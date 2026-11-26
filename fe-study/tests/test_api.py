import json

from conftest import answer_all, start


def test_home_empty(client):
    h = client.get("/api/home").get_json()
    assert h["active_session"] is None
    assert h["pool"] == {"study": 69, "study_unseen": 69, "mock": 10, "review": 0}
    assert h["today"]["answered"] == 0 and h["streak_days"] == 0
    assert h["settings"]["session_size"] == 8
    assert client.get("/api/home").headers["Cache-Control"] == "no-store"


def test_session_flow(client):
    s = start(client, mode="study")
    assert len(s["questions"]) == 8 and s["status"] == "active"
    q = s["questions"][0]
    assert q["attempt"] is None and "answer" not in q
    r = client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "イ", "time_ms": 5000}).get_json()
    assert r == {"is_correct": False, "answer": "ア", "explanation_url": f"http://exp/{q['id']}"}
    # 上書き
    r = client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "ア", "time_ms": 6000}).get_json()
    assert r["is_correct"] is True
    cur = client.get("/api/sessions/active").get_json()
    assert cur["current_index"] == 1
    assert cur["questions"][0]["attempt"] == {"chosen": "ア", "is_correct": True, "time_ms": 6000, "answer": "ア"}
    assert client.get("/api/home").get_json()["active_session"] == {
        "id": s["id"], "mode": "study", "part": "A", "current_index": 1, "total": 8}
    for qq in s["questions"][1:]:
        client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": qq["id"], "chosen": "イ", "time_ms": 1000})
    f = client.post(f"/api/sessions/{s['id']}/finish").get_json()
    assert f["total"] == 8 and f["correct"] == 1 and f["passed"] is None
    assert f["time_ms"] == 6000 + 7000
    assert sum(c["total"] for c in f["by_category"]) == 8
    assert client.get("/api/sessions/active").get_json() is None
    h = client.get("/api/home").get_json()
    assert h["today"]["answered"] == 8 and h["streak_days"] == 1


def test_answer_validation(client):
    s = start(client, mode="study")
    url = f"/api/sessions/{s['id']}/answer"
    assert client.post(url, json={"question_id": "nope", "chosen": "ア", "time_ms": 1}).status_code == 400
    assert client.post(url, json={"question_id": s["questions"][0]["id"], "chosen": "Z", "time_ms": 1}).status_code == 400
    assert client.get("/api/sessions/9999").status_code == 404


def test_autosave_progress(client):
    s = start(client, mode="study")
    r = client.post(f"/api/sessions/{s['id']}/progress", json={"current_index": 2, "current_elapsed_ms": 15300})
    assert r.status_code == 200
    # sendBeacon: text/plain
    r = client.post(f"/api/sessions/{s['id']}/progress", data=json.dumps(
        {"current_index": 3, "current_elapsed_ms": 4200}), content_type="text/plain;charset=UTF-8")
    assert r.status_code == 200
    a = client.get("/api/sessions/active").get_json()
    assert a["current_index"] == 3 and a["current_elapsed_ms"] == 4200
    assert client.post(f"/api/sessions/{s['id']}/progress", json={"current_index": 99, "current_elapsed_ms": 0}).status_code == 400


def test_new_session_abandons_active(client):
    a = start(client, mode="study")
    b = start(client, mode="study")
    assert client.get(f"/api/sessions/{a['id']}").get_json()["status"] == "abandoned"
    assert client.get("/api/sessions/active").get_json()["id"] == b["id"]


def test_mock_hides_answers(client):
    s = start(client, mode="mock", exam_code="05_haru")
    assert len(s["questions"]) == 10 and s["time_limit_sec"] == 900
    q = s["questions"][0]
    r = client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "ア", "time_ms": 3000}).get_json()
    assert r == {"saved": True}
    cur = client.get(f"/api/sessions/{s['id']}").get_json()
    assert cur["questions"][0]["attempt"] == {"chosen": "ア", "time_ms": 3000}
    assert "answer" not in json.dumps(cur["questions"][0]["attempt"])
    assert client.get(f"/api/questions/{q['id']}").get_json()["answer"] is None
    assert client.get("/api/history").get_json() == []
    f = client.post(f"/api/sessions/{s['id']}/finish").get_json()
    assert f["total"] == 10 and f["correct"] == 1 and f["passed"] is False
    assert f["questions"][0]["attempt"]["answer"] == "ア"
    assert f["questions"][1]["attempt"] is None  # 未回答
    assert client.get(f"/api/questions/{q['id']}").get_json()["answer"] == "ア"
    assert len(client.get("/api/history").get_json()) == 1


def test_mock_random_and_pass(client):
    s = start(client, mode="mock")
    assert len(s["questions"]) == 10  # プールが10問のみ
    answer_all(client, s)
    assert client.post(f"/api/sessions/{s['id']}/finish").get_json()["passed"] is True


def test_review_only_wrong(client):
    assert client.post("/api/sessions", json={"mode": "review"}).status_code == 409
    s = start(client, mode="study", size=10)
    ids = [q["id"] for q in s["questions"]]
    for i, q in enumerate(s["questions"]):
        client.post(f"/api/sessions/{s['id']}/answer",
                    json={"question_id": q["id"], "chosen": "イ" if i < 3 else "ア", "time_ms": 1000})
    wrong = set(ids[:3])
    client.post(f"/api/sessions/{s['id']}/finish")
    assert client.get("/api/home").get_json()["pool"]["review"] == 3
    r = start(client, mode="review")
    assert {q["id"] for q in r["questions"]} == wrong


def test_session_category_override(client):
    st = client.get("/api/settings").get_json()
    s = start(client, mode="study", category_ids=[5, 6])
    assert {q["category_id"] for q in s["questions"]} <= {5, 6}
    assert client.get("/api/settings").get_json() == st  # settings は変更されない
    for bad in ([0], [24], "3", [1.5], [True]):
        assert client.post("/api/sessions", json={"mode": "study", "category_ids": bad}).status_code == 400
    s = start(client, mode="study", category_ids=[5])
    for q in s["questions"]:
        client.post(f"/api/sessions/{s['id']}/answer", json={"question_id": q["id"], "chosen": "イ", "time_ms": 1})
    client.post(f"/api/sessions/{s['id']}/finish")
    r = start(client, mode="review", category_ids=[5])
    assert {q["category_id"] for q in r["questions"]} == {5}
    assert client.post("/api/sessions", json={"mode": "review", "category_ids": [6]}).status_code == 409


def test_settings(client):
    r = client.put("/api/settings", json={"session_size": 10, "theme": "dark", "category_ids": [3, 1]})
    s = r.get_json()
    assert s["session_size"] == 10 and s["theme"] == "dark" and s["category_ids"] == [1, 3]
    assert client.get("/api/settings").get_json()["include_calc"] is True
    for bad in ({"session_size": 4}, {"session_size": 16}, {"foo": 1}, {"theme": "x"},
                {"font_scale": 2}, {"include_calc": 1}, {"category_ids": [99]}, {"target_sec": 1}):
        assert client.put("/api/settings", json=bad).status_code == 400, bad
    assert client.put("/api/settings", json={"font_scale": 1.15}).get_json()["font_scale"] == 1.15
    s = start(client, mode="study")
    assert len(s["questions"]) == 6 and {q["category_id"] for q in s["questions"]} <= {1, 3}


def test_history_and_question(client):
    s = start(client, mode="study", size=5)
    answer_all(client, s)
    h = client.get("/api/history?limit=3").get_json()
    assert len(h) == 3 and h[0]["answer"] == "ア" and h[0]["mode"] == "study"
    q = client.get(f"/api/questions/{s['questions'][0]['id']}").get_json()
    assert q["answer"] == "ア" and len(q["attempts"]) == 1
    assert client.get("/api/questions/none").status_code == 404


def test_media_and_static(client, app):
    assert client.get("/media/ok.png").data == b"PNG"
    assert client.get("/media/..%2Fsecret.txt").status_code == 404
    assert client.get("/media/%2e%2e/secret.txt").status_code == 404
    assert client.get("/media/../secret.txt").status_code == 404
    assert client.get("/media/nope.png").status_code == 404
    assert client.get("/static/../app/db.py").status_code == 404
    assert client.get("/api/nothing").get_json() is not None


def test_home_lists_mock_exams(client):
    d = client.get("/api/home").get_json()
    assert d["mock_exams"], "模試の回一覧が空"
    for e in d["mock_exams"]:
        assert e["exam_code"][:2] in ("05", "06", "07", "08")
        assert e["count"] > 0


def test_service_worker_served_from_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers.get("Service-Worker-Allowed") == "/"
