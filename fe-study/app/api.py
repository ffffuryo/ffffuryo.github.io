import json

from flask import Blueprint, current_app, jsonify, request

from . import selection, stats
from .db import get_settings, set_settings

bp = Blueprint("api", __name__, url_prefix="/api")

LABELS = ("ア", "イ", "ウ", "エ")


class ApiError(Exception):
    def __init__(self, msg, code=400):
        self.msg, self.code = msg, code


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify({"error": e.msg}), e.code


def db():
    return current_app.get_db()


def body_json():
    d = request.get_json(force=True, silent=True)
    if d is None and not request.get_data():
        return {}
    if not isinstance(d, dict):
        raise ApiError("JSON オブジェクトが必要です")
    return d


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_category_ids(v):
    if not isinstance(v, list) or not all(is_int(x) and 1 <= x <= 23 for x in v):
        raise ApiError("category_ids は 1〜23 の整数配列で指定してください")
    return sorted(set(v))


# ---- settings ----
def validate_settings(d):
    out = {}
    for k, v in d.items():
        if k == "include_calc":
            if not isinstance(v, bool):
                raise ApiError("include_calc は真偽値です")
        elif k == "session_size":
            if not is_int(v) or not 5 <= v <= 15:
                raise ApiError("session_size は 5〜15 です")
        elif k == "category_ids":
            v = validate_category_ids(v)
        elif k == "font_scale":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) not in (0.9, 1.0, 1.15, 1.3):
                raise ApiError("font_scale は 0.9/1.0/1.15/1.3 です")
            v = float(v)
        elif k == "theme":
            if v not in ("auto", "light", "dark"):
                raise ApiError("theme は auto/light/dark です")
        elif k == "target_sec":
            if not is_int(v) or not 10 <= v <= 600:
                raise ApiError("target_sec は 10〜600 です")
        else:
            raise ApiError(f"未知の設定キー: {k}")
        out[k] = v
    return out


@bp.get("/settings")
def settings_get():
    return jsonify(get_settings(db()))


@bp.put("/settings")
def settings_put():
    vals = validate_settings(body_json())
    set_settings(db(), vals)
    return jsonify(get_settings(db()))


# ---- helpers ----
def load_session(sid):
    s = db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise ApiError("セッションが見つかりません", 404)
    return s


def reveal_ok(s):
    return s["mode"] != "mock" or s["status"] == "done"


def session_object(s):
    conn = db()
    ids = json.loads(s["question_ids"])
    qs = {}
    if ids:
        for r in conn.execute(
                f"SELECT q.*, c.name cname FROM questions q JOIN categories c ON c.id=q.category_id "
                f"WHERE q.id IN ({','.join('?' * len(ids))})", ids):
            qs[r["id"]] = r
    atts = {r["question_id"]: r for r in conn.execute(
        "SELECT * FROM attempts WHERE session_id=? AND chosen IS NOT NULL", (s["id"],))}
    reveal = reveal_ok(s)
    questions = []
    for qid in ids:
        q = qs[qid]
        a = atts.get(qid)
        att = None
        if a:
            att = {"chosen": a["chosen"], "time_ms": a["time_ms"]}
            if reveal:
                att["is_correct"] = bool(a["is_correct"])
                att["answer"] = q["answer"]
        questions.append({
            "id": q["id"], "exam_label": q["exam_label"], "number": q["number"],
            "category_id": q["category_id"], "category_name": q["cname"], "body": q["body"],
            "choices": json.loads(q["choices"]),
            "image_url": f"/media/{q['image_path']}" if q["image_path"] else None,
            "is_calc": bool(q["is_calc"]), "source_label": q["source_label"],
            "explanation_url": q["explanation_url"], "attempt": att,
        })
    remaining = None
    if s["time_limit_sec"] is not None:
        used = conn.execute("SELECT COALESCE(SUM(time_ms),0) FROM attempts WHERE session_id=?",
                            (s["id"],)).fetchone()[0] + s["current_elapsed_ms"]
        remaining = max(0, s["time_limit_sec"] - used // 1000)
    return {"id": s["id"], "mode": s["mode"], "status": s["status"],
            "current_index": s["current_index"], "current_elapsed_ms": s["current_elapsed_ms"],
            "time_limit_sec": s["time_limit_sec"], "started_at": s["started_at"],
            "remaining_sec": remaining, "questions": questions}


# ---- home ----
@bp.get("/home")
def home():
    conn = db()
    s = conn.execute("SELECT * FROM sessions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    active = None
    if s:
        active = {"id": s["id"], "mode": s["mode"], "current_index": s["current_index"],
                  "total": len(json.loads(s["question_ids"]))}
    one = lambda sql: conn.execute(sql).fetchone()[0]
    pool = {
        "study": one("SELECT COUNT(*) FROM questions WHERE pool='study'"),
        "study_unseen": one(
            "SELECT COUNT(*) FROM questions q WHERE q.pool='study' "
            "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id=q.id AND a.chosen IS NOT NULL)"),
        "mock": one("SELECT COUNT(*) FROM questions WHERE pool='mock'"),
        "review": len(selection.review_ids(conn)),
    }
    mock_exams = [{"exam_code": r["exam_code"], "exam_label": r["exam_label"], "count": r["n"]}
                  for r in conn.execute(
                      "SELECT exam_code, exam_label, COUNT(*) n FROM questions WHERE pool='mock' "
                      "GROUP BY exam_code, exam_label ORDER BY exam_code DESC")]
    return jsonify({"active_session": active, "today": stats.today_summary(conn),
                    "mock_exams": mock_exams,
                    "streak_days": stats.streak_days(conn), "settings": get_settings(conn),
                    "pool": pool, "weak_categories": stats.weak_categories(conn)})


# ---- sessions ----
@bp.post("/sessions")
def create_session():
    conn = db()
    d = body_json()
    mode = d.get("mode")
    if mode not in ("study", "review", "mock"):
        raise ApiError("mode は study/review/mock です")
    st = get_settings(conn)
    size = d.get("size")
    if size is not None and (not is_int(size) or not 1 <= size <= 100):
        raise ApiError("size が不正です")
    cat_ids = validate_category_ids(d["category_ids"]) if "category_ids" in d else st["category_ids"]
    rng = current_app.config["RNG"]
    opts = {}
    limit = None
    if mode == "study":
        size = size or st["session_size"]
        ids = selection.pick_study(conn, size, st["include_calc"], cat_ids, rng, st["target_sec"])
        opts = {"size": size, "include_calc": st["include_calc"], "category_ids": cat_ids}
    elif mode == "review":
        ids = selection.pick_review(conn, size, cat_ids, rng)
        opts = {"size": size, "category_ids": cat_ids}
    else:
        ec = d.get("exam_code")
        if ec is not None and not isinstance(ec, str):
            raise ApiError("exam_code が不正です")
        ids = selection.pick_mock(conn, ec, size or 20, rng)
        limit = selection.mock_time_limit(len(ids))
        opts = {"exam_code": ec, "size": len(ids)}
    if not ids:
        raise ApiError("出題できる問題がありません", 409)
    conn.execute("UPDATE sessions SET status='abandoned', ended_at=datetime('now','localtime') "
                 "WHERE status='active'")
    cur = conn.execute(
        "INSERT INTO sessions(mode,question_ids,time_limit_sec,options) VALUES(?,?,?,?)",
        (mode, json.dumps(ids), limit, json.dumps(opts, ensure_ascii=False)))
    conn.commit()
    return jsonify(session_object(load_session(cur.lastrowid))), 201


@bp.get("/sessions/active")
def active_session():
    s = db().execute("SELECT * FROM sessions WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    return jsonify(session_object(s) if s else None)


@bp.get("/sessions/<int:sid>")
def get_session(sid):
    return jsonify(session_object(load_session(sid)))


@bp.post("/sessions/<int:sid>/answer")
def answer(sid):
    conn = db()
    s = load_session(sid)
    if s["status"] != "active":
        raise ApiError("このセッションは終了しています", 409)
    d = body_json()
    qid, chosen, t = d.get("question_id"), d.get("chosen"), d.get("time_ms")
    ids = json.loads(s["question_ids"])
    if qid not in ids:
        raise ApiError("このセッションの問題ではありません")
    if chosen not in LABELS:
        raise ApiError("chosen が不正です")
    if not is_int(t) or t < 0:
        raise ApiError("time_ms が不正です")
    q = conn.execute("SELECT answer, explanation_url FROM questions WHERE id=?", (qid,)).fetchone()
    ok = int(chosen == q["answer"])
    conn.execute(
        "INSERT INTO attempts(session_id,question_id,chosen,is_correct,time_ms) VALUES(?,?,?,?,?) "
        "ON CONFLICT(session_id,question_id) DO UPDATE SET chosen=excluded.chosen, "
        "is_correct=excluded.is_correct, time_ms=excluded.time_ms, answered_at=datetime('now','localtime')",
        (sid, qid, chosen, ok, t))
    nxt = max(s["current_index"], ids.index(qid) + 1)
    conn.execute("UPDATE sessions SET current_index=?, current_elapsed_ms=0, "
                 "updated_at=datetime('now','localtime') WHERE id=?", (nxt, sid))
    conn.commit()
    if s["mode"] == "mock":
        return jsonify({"saved": True})
    return jsonify({"is_correct": bool(ok), "answer": q["answer"], "explanation_url": q["explanation_url"]})


@bp.post("/sessions/<int:sid>/progress")
def progress(sid):
    conn = db()
    s = load_session(sid)
    d = body_json()
    idx, el = d.get("current_index"), d.get("current_elapsed_ms")
    total = len(json.loads(s["question_ids"]))
    if not is_int(idx) or not 0 <= idx <= total or not is_int(el) or el < 0:
        raise ApiError("current_index / current_elapsed_ms が不正です")
    if s["status"] == "active":
        conn.execute("UPDATE sessions SET current_index=?, current_elapsed_ms=?, "
                     "updated_at=datetime('now','localtime') WHERE id=?", (idx, el, sid))
        conn.commit()
    return jsonify({"ok": True})


@bp.post("/sessions/<int:sid>/finish")
def finish(sid):
    conn = db()
    s = load_session(sid)
    if s["status"] == "abandoned":
        raise ApiError("このセッションは中断されています", 409)
    ids = json.loads(s["question_ids"])
    if s["status"] == "active":
        for qid in ids:
            conn.execute("INSERT OR IGNORE INTO attempts(session_id,question_id,chosen,is_correct,time_ms) "
                         "VALUES(?,?,NULL,0,0)", (sid, qid))
        conn.execute("UPDATE sessions SET status='done', ended_at=datetime('now','localtime'), "
                     "updated_at=datetime('now','localtime') WHERE id=?", (sid,))
        conn.commit()
        s = load_session(sid)
    obj = session_object(s)
    total = len(obj["questions"])
    correct = sum(1 for q in obj["questions"] if q["attempt"] and q["attempt"]["is_correct"])
    time_ms = conn.execute("SELECT COALESCE(SUM(time_ms),0) FROM attempts WHERE session_id=?",
                           (sid,)).fetchone()[0]
    cats = {}
    for q in obj["questions"]:
        c = cats.setdefault(q["category_id"], {"category_id": q["category_id"], "name": q["category_name"],
                                               "total": 0, "correct": 0})
        c["total"] += 1
        c["correct"] += 1 if q["attempt"] and q["attempt"]["is_correct"] else 0
    return jsonify({
        "total": total, "correct": correct, "time_ms": time_ms,
        "avg_time_ms": time_ms // total if total else 0,
        "by_category": sorted(cats.values(), key=lambda c: c["category_id"]),
        "questions": obj["questions"],
        "passed": (correct / total >= 0.6) if s["mode"] == "mock" and total else None,
    })


# ---- stats / history / question ----
@bp.get("/stats/categories")
def stats_categories():
    return jsonify(stats.category_stats(db()))


@bp.get("/stats/time")
def stats_time():
    return jsonify(stats.time_stats(db()))


@bp.get("/history")
def history():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        raise ApiError("limit/offset が不正です")
    rows = db().execute(
        "SELECT a.question_id, a.chosen, a.is_correct, a.time_ms, a.answered_at, s.mode, s.status, "
        "q.answer, q.exam_label, q.number, c.name cname FROM attempts a "
        "JOIN sessions s ON s.id=a.session_id JOIN questions q ON q.id=a.question_id "
        "JOIN categories c ON c.id=q.category_id "
        "WHERE a.chosen IS NOT NULL AND (s.mode!='mock' OR s.status='done') "
        "ORDER BY a.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return jsonify([{
        "question_id": r["question_id"], "label": f"{r['exam_label']} 問{r['number']}",
        "category_name": r["cname"], "chosen": r["chosen"], "answer": r["answer"],
        "is_correct": bool(r["is_correct"]), "time_ms": r["time_ms"],
        "answered_at": r["answered_at"], "mode": r["mode"]} for r in rows])


@bp.get("/questions/<qid>")
def get_question(qid):
    conn = db()
    q = conn.execute("SELECT q.*, c.name cname FROM questions q JOIN categories c ON c.id=q.category_id "
                     "WHERE q.id=?", (qid,)).fetchone()
    if not q:
        raise ApiError("問題が見つかりません", 404)
    reveal = q["pool"] == "study" or conn.execute(
        "SELECT 1 FROM sessions s WHERE s.status='done' AND s.mode='mock' "
        "AND EXISTS (SELECT 1 FROM json_each(s.question_ids) j WHERE j.value=?)", (qid,)).fetchone()
    atts = conn.execute(
        "SELECT a.chosen, a.is_correct, a.time_ms, a.answered_at, s.mode FROM attempts a "
        "JOIN sessions s ON s.id=a.session_id WHERE a.question_id=? AND a.chosen IS NOT NULL "
        "AND (s.mode!='mock' OR s.status='done') ORDER BY a.id DESC", (qid,)).fetchall() if reveal else []
    return jsonify({
        "id": q["id"], "exam_label": q["exam_label"], "number": q["number"],
        "category_id": q["category_id"], "category_name": q["cname"], "body": q["body"],
        "choices": json.loads(q["choices"]),
        "image_url": f"/media/{q['image_path']}" if q["image_path"] else None,
        "is_calc": bool(q["is_calc"]), "pool": q["pool"], "source_label": q["source_label"],
        "explanation_url": q["explanation_url"],
        "answer": q["answer"] if reveal else None,
        "attempts": [{"chosen": a["chosen"], "is_correct": bool(a["is_correct"]), "time_ms": a["time_ms"],
                      "answered_at": a["answered_at"], "mode": a["mode"]} for a in atts]})
