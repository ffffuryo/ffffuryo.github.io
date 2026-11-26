"""Python 版 API と JS ローカル API（static/local/*.js）のパリティ検証。

  python tests/js/parity.py      （fe-study/ で実行。不一致があれば exit 1）
  pytest tests/test_parity_js.py

手順:
  1. conftest と同じ固定データ（A 69+10、B 8+3、グループあり）と、乱数で作った固定の sessions/attempts を SQLite に作る
  2. Python の Flask アプリ（RNG は台本つき）で「読み取り API 一式」と「セッションを作って解いて終える操作列」を実行・記録
  3. 同じデータを JSON で Node に渡し、同じ rng 列・同じ now で FELocal.api に同じ操作を再生
  4. 結果を比較（浮動小数は 1e-9。操作列の started_at / answered_at は Python 側が実時計なのでマスク）
乱数に依存する出題も、同じ rng 列を与えるため選ばれる問題・順序まで一致するはず。
"""
import json
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import create_app  # noqa: E402
from app.db import connect, get_settings, init_db  # noqa: E402

MASK_KEYS = {"started_at", "answered_at"}


class ScriptRng:
    """JS 側と同じ台本の乱数。shuffle は JS の Fisher-Yates と同じ手順"""

    def __init__(self, vals):
        self.vals, self.i = vals, 0

    def random(self):
        v = self.vals[self.i % len(self.vals)]
        self.i += 1
        return v

    def shuffle(self, x):
        for i in range(len(x) - 1, 0, -1):
            j = int(self.random() * (i + 1))
            x[i], x[j] = x[j], x[i]


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_fixture(path, now_dt):
    """conftest.py と同じ問題データ + 固定の履歴。問題は id 順に挿入（Python/JS の走査順を揃える）"""
    from conftest import _ins, _ins_b, CALC

    scratch = str(Path(path).with_suffix(".scratch.db"))
    init_db(scratch)
    c = connect(scratch)
    for cid in range(1, 24):
        for i in range(1, 4):
            _ins(c, f"fe-30_aki-q{cid:02d}{i}", "30_aki", "平成30年秋期", cid * 10 + i, cid, int((cid, i) in CALC), "study")
    for n in range(1, 11):
        _ins(c, f"fe-05_haru-q{n:02d}", "05_haru", "令和5年春期", n, (n - 1) % 23 + 1, 0, "mock")
    _ins_b(c, "feb-sample-q01", "sample", "サンプル", 1, "pseudo", "study", tags=["トレース", "再帰"], diff="やさしい")
    _ins_b(c, "feb-sample-q02", "sample", "サンプル", 2, "pseudo", "study", tags=["トレース"], diff="ふつう", answer="コ")
    _ins_b(c, "feb-sample-q03", "sample", "サンプル", 3, "pseudo", "study", tags=["一次元配列"], diff="難しい", expl=False)
    _ins_b(c, "feb-sample-q04", "sample", "サンプル", 4, "security", "study", tags=["暗号"], diff="ふつう")
    _ins_b(c, "feb-sample-q05", "sample", "サンプル", 5, "security", "study", tags=["暗号", "認証"])
    for i, sub in ((3, "設問3(2)"), (1, "設問1"), (2, "設問2")):
        _ins_b(c, f"feb-furoku3-q01-{i}", "furoku3", "付録3", 1, "security", "study",
               group="furoku3-q01", sub=sub, tags=["認証"], diff="難しい")
    # 設問番号の桁違い（設問2 < 設問10）を確認するグループ
    for i, sub in ((1, "設問10"), (2, "設問2"), (3, "設問1")):
        _ins_b(c, f"feb-furoku2-q07-{i}", "furoku2", "付録2", 7, "pseudo", "study", group="furoku2-q07", sub=sub,
               tags=["トレース", "二次元配列"], diff="ふつう")
    for n in range(1, 4):
        _ins_b(c, f"feb-05_haru-q{n:02d}", "05_haru", "令和5年", n, "pseudo", "mock", tags=["トレース"])
    c.commit()
    cols = [r[1] for r in c.execute("PRAGMA table_info(questions)")]
    rows = c.execute("SELECT * FROM questions ORDER BY id").fetchall()
    c.close()
    init_db(path)
    d = connect(path)
    for r in rows:
        d.execute(f"INSERT INTO questions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(r))

    rnd = random.Random(20261126)
    all_q = {r["id"]: dict(r) for r in rows}
    ids_of = lambda pool, part: [i for i, q in all_q.items() if q["pool"] == pool and q["part"] == part]
    study_a, study_b = ids_of("study", "A"), ids_of("study", "B")
    day_pool = [0, 0, 0, 1, 1, 2, 3, 5, 8, 13, 20, 28, 29, 30, 35, 45]
    sid = [0]
    aid = [0]

    def when(off_days):
        t = now_dt - timedelta(days=off_days)
        return fmt(t.replace(hour=rnd.randint(0, 23), minute=rnd.randint(0, 59), second=rnd.randint(0, 59))
                   if off_days else t - timedelta(minutes=rnd.randint(0, 300)))

    def session(mode, ids, status="done", answered=None, limit=None, opts=None, cur_idx=None, elapsed=0, p_ok=0.6):
        sid[0] += 1
        off = rnd.choice(day_pool)
        started = when(off)
        d.execute("INSERT INTO sessions(id,mode,status,question_ids,current_index,current_elapsed_ms,time_limit_sec,options,"
                  "started_at,updated_at,ended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (sid[0], mode, status, json.dumps(ids), cur_idx if cur_idx is not None else (len(ids) if status == "done" else 0),
                   elapsed, limit, json.dumps(opts or {}), started, started, started if status != "active" else None))
        n = len(ids) if answered is None else answered
        for k, qid in enumerate(ids):
            aid[0] += 1
            q = all_q[qid]
            if k < n:
                labels = "アイウエオカキクケコ" if q["part"] == "B" else "アイウエ"
                ok = rnd.random() < p_ok
                ch = q["answer"] if ok else rnd.choice([x for x in labels if x != q["answer"]])
                t = rnd.choice([0, 1500, 8000, 29999, 30000, 45000, 59999, 60000, 89999, 90000, 119999, 120000, 250000,
                                rnd.randint(2000, 200000), rnd.randint(2000, 400000)])
                d.execute("INSERT INTO attempts(id,session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                          "VALUES(?,?,?,?,?,?,?)", (aid[0], sid[0], qid, ch, int(ch == q["answer"]), t, when(off)))
            elif status == "done":
                d.execute("INSERT INTO attempts(id,session_id,question_id,chosen,is_correct,time_ms,answered_at) "
                          "VALUES(?,?,?,NULL,0,0,?)", (aid[0], sid[0], qid, started))
            else:
                aid[0] -= 1

    for _ in range(14):
        session("study", rnd.sample(study_a, 8), opts={"size": 8})
    for _ in range(3):
        session("review", rnd.sample(study_a, 5), p_ok=0.8)
    for _ in range(5):
        ids = rnd.sample(study_b, rnd.randint(1, 4))
        session("study", ids, opts={"part": "B"}, p_ok=0.5)
    mock_a = sorted(ids_of("mock", "A"))
    session("mock", mock_a, answered=7, limit=900, opts={"exam_code": "05_haru"}, p_ok=0.7)
    session("mock", mock_a[:5], answered=5, limit=450, p_ok=0.4)
    session("mock", sorted(ids_of("mock", "B")), answered=2, limit=900, opts={"part": "B"})
    session("mock", mock_a, status="abandoned", answered=3, limit=900, cur_idx=3)  # 途中放棄の模試: 履歴に出ない
    session("study", rnd.sample(study_a, 8), status="abandoned", answered=2, cur_idx=2)
    session("mock", mock_a, status="active", answered=4, limit=900, cur_idx=4, elapsed=20000, p_ok=0.7)
    d.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('target_sec','75')")
    d.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('theme','\"dark\"')")
    d.commit()
    d.close()
    Path(scratch).unlink(missing_ok=True)
    return sorted(all_q)


def pack_format(path):
    """DB の中身を JS（SPEC 7.2/7.3 形式）へ"""
    c = connect(path)
    qs = []
    for r in c.execute("SELECT * FROM questions ORDER BY id"):
        q = dict(r)
        for k in ("choices", "images", "tags", "explanation_images"):
            q[k] = json.loads(q[k]) if q[k] else None
        q["is_calc"] = bool(q["is_calc"])
        qs.append(q)
    ss = []
    for r in c.execute("SELECT * FROM sessions ORDER BY id"):
        s = dict(r)
        s["question_ids"] = json.loads(s["question_ids"])
        s["options"] = json.loads(s["options"])
        ss.append(s)
    at = [dict(r) for r in c.execute("SELECT * FROM attempts ORDER BY id")]
    settings = {r["key"]: json.loads(r["value"]) for r in c.execute("SELECT key,value FROM settings")}
    c.close()
    return qs, ss, at, settings


class Recorder:
    def __init__(self, client):
        self.client, self.reqs, self.resps = client, [], []

    def __call__(self, method, path, body=None):
        kw = {} if body is None else {"json": body}
        r = self.client.open(path, method=method, **kw)
        data = r.get_json(silent=True)
        if r.status_code >= 400:
            data = {"__error": True, "status": r.status_code, "error": (data or {}).get("error")}
        self.reqs.append({"method": method, "path": path, "body": body})
        self.resps.append(data)
        return data


def read_requests(ids, sess_ids):
    reqs = [("GET", "/api/home"), ("GET", "/api/settings"), ("GET", "/api/sessions/active"),
            ("GET", "/api/stats/categories"), ("GET", "/api/stats/categories?part=A"), ("GET", "/api/stats/categories?part=B"),
            ("GET", "/api/stats/categories?part=Z"),
            ("GET", "/api/stats/time"), ("GET", "/api/stats/time?part=A"), ("GET", "/api/stats/time?part=B"),
            ("GET", "/api/stats/time?part=all"), ("GET", "/api/stats/time?part=X"), ("GET", "/api/stats/b"),
            ("GET", "/api/history"), ("GET", "/api/history?limit=7&offset=3"), ("GET", "/api/history?limit=9999"),
            ("GET", "/api/history?limit=0"), ("GET", "/api/history?limit=x"), ("GET", "/api/history?offset=-5"),
            ("GET", "/api/history?limit=3&offset=1000"), ("GET", "/api/sessions/99999")]
    reqs += [("GET", f"/api/sessions/{i}") for i in sess_ids]
    reqs += [("POST", f"/api/sessions/{i}/finish") for i in sess_ids if i not in ()]  # done/abandoned のみ後で除外
    reqs += [("GET", f"/api/questions/{q}") for q in ids] + [("GET", "/api/questions/none")]
    return reqs


def scenario(rec):
    """セッションを作って解く操作列。回答は問題ごとに決め打ちのパターン（正解・不正解・速い・遅い）"""
    def solve(sess, plan, finish=True, upto=None):
        qs = sess["questions"][:upto]
        for k, q in enumerate(qs):
            labels = [c["label"] for c in q["choices"]]
            kind = plan[k % len(plan)]
            ans = {"ok": None, "ng": labels[1], "slow": None}[kind]
            if ans is None:  # 正解を知るには finish か静的データが必要 → 一旦 'ア' / B の q02 は 'コ'
                ans = "コ" if q["id"] == "feb-sample-q02" else "ア"
            t = {"ok": 20000, "ng": 5000, "slow": 150000}[kind] + k * 111
            rec("POST", f"/api/sessions/{sess['id']}/answer", {"question_id": q["id"], "chosen": ans, "time_ms": t})
            if k % 3 == 0:
                rec("POST", f"/api/sessions/{sess['id']}/progress", {"current_index": min(k + 1, len(qs)), "current_elapsed_ms": 1234 + k})
        if finish:
            rec("POST", f"/api/sessions/{sess['id']}/finish")

    rec("GET", "/api/home")
    s = rec("POST", "/api/sessions", {"mode": "study"})
    solve(s, ["ok", "ng", "slow", "ok"])
    rec("GET", "/api/home")
    s = rec("POST", "/api/sessions", {"mode": "study", "size": 15, "category_ids": [3, 4, 5, 11, 12]})
    solve(s, ["ng", "ok", "ng"], finish=False, upto=9)
    rec("GET", "/api/sessions/active")
    s = rec("POST", "/api/sessions", {"mode": "study", "size": 12})
    solve(s, ["ng", "ng", "ok", "slow"])
    s = rec("POST", "/api/sessions", {"mode": "review"})
    solve(s, ["ok", "ng"])
    s = rec("POST", "/api/sessions", {"mode": "review", "size": 3, "category_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]})
    solve(s, ["ng"], finish=False)
    rec("POST", f"/api/sessions/{s['id']}/finish")
    rec("PUT", "/api/settings", {"include_calc": False, "session_size": 11, "target_sec": 40, "category_ids": [9, 2, 2, 3]})
    s = rec("POST", "/api/sessions", {"mode": "study"})
    solve(s, ["ok", "slow", "ng"])
    rec("PUT", "/api/settings", {"include_calc": True, "session_size": 8, "category_ids": [], "target_sec": 90})
    s = rec("POST", "/api/sessions", {"mode": "mock", "size": 6})
    solve(s, ["ok", "ng", "ok"], finish=False, upto=4)
    rec("GET", f"/api/sessions/{s['id']}")
    rec("GET", "/api/history?limit=10")
    rec("POST", f"/api/sessions/{s['id']}/finish")
    s = rec("POST", "/api/sessions", {"mode": "mock", "exam_code": "05_haru"})
    solve(s, ["ok"])
    rec("POST", "/api/sessions", {"mode": "mock", "exam_code": "nope"})
    # 科目B
    for size in (1, 2, 3, 6):
        s = rec("POST", "/api/sessions", {"mode": "study", "part": "B", "size": size})
        solve(s, ["ok", "ng", "slow"])
    rec("PUT", "/api/settings", {"b_topics": ["security"], "session_size_b": 3, "target_sec_b": 120})
    s = rec("POST", "/api/sessions", {"mode": "study", "part": "B"})
    solve(s, ["ng", "ok"])
    s = rec("POST", "/api/sessions", {"mode": "study", "part": "B", "b_topics": ["pseudo"], "size": 4})
    solve(s, ["ok", "ok", "ng"], finish=False, upto=2)
    s = rec("POST", "/api/sessions", {"mode": "review", "part": "B"})
    solve(s, ["ok"])
    s = rec("POST", "/api/sessions", {"mode": "review", "part": "B", "size": 1})
    s = rec("POST", "/api/sessions", {"mode": "mock", "part": "B"})
    solve(s, ["ok", "ng"], finish=False, upto=2)
    rec("POST", f"/api/sessions/{s['id']}/finish")
    s = rec("POST", "/api/sessions", {"mode": "mock", "part": "B", "exam_code": "05_haru"})
    solve(s, ["ok"])
    rec("POST", "/api/sessions", {"mode": "mock", "part": "B", "size": 2})
    # エラー系
    s = rec("POST", "/api/sessions", {"mode": "study"})
    q0 = s["questions"][0]["id"]
    for body in ({"question_id": q0, "chosen": "オ", "time_ms": 1}, {"question_id": "zz", "chosen": "ア", "time_ms": 1},
                 {"question_id": q0, "chosen": "ア", "time_ms": -3}, {"question_id": q0, "chosen": "ア", "time_ms": 1.5},
                 {"question_id": q0, "chosen": "ア"}, {}):
        rec("POST", f"/api/sessions/{s['id']}/answer", body)
    rec("POST", f"/api/sessions/{s['id']}/progress", {"current_index": 99, "current_elapsed_ms": 0})
    rec("POST", f"/api/sessions/{s['id']}/progress", {"current_index": 1, "current_elapsed_ms": -1})
    rec("POST", f"/api/sessions/{s['id']}/progress", {"current_index": 3, "current_elapsed_ms": 999})
    rec("POST", f"/api/sessions/{s['id']}/finish")
    rec("POST", f"/api/sessions/{s['id']}/answer", {"question_id": q0, "chosen": "ア", "time_ms": 1})
    rec("POST", f"/api/sessions/{s['id']}/progress", {"current_index": 1, "current_elapsed_ms": 5})
    rec("POST", f"/api/sessions/{s['id']}/finish")
    for body in ({"mode": "x"}, {"mode": "study", "part": "C"}, {"mode": "study", "part": None}, {"mode": "study", "size": 0},
                 {"mode": "study", "size": 101}, {"mode": "study", "size": True}, {"mode": "study", "category_ids": [0]},
                 {"mode": "study", "category_ids": "3"}, {"mode": "mock", "exam_code": 5}, {"mode": "study", "part": "B", "b_topics": ["z"]},
                 {"mode": "mock", "part": "B", "exam_code": 3}, {}):
        rec("POST", "/api/sessions", body)
    for body in ({"session_size": 4}, {"session_size": 16}, {"foo": 1}, {"theme": "x"}, {"font_scale": 2}, {"font_scale": 1.15},
                 {"include_calc": 1}, {"category_ids": [99]}, {"target_sec": 1}, {"target_sec": 600}, {"session_size_b": 0},
                 {"session_size_b": 5}, {"b_topics": ["x"]}, {"b_topics": "pseudo"}, {"b_topics": ["security", "pseudo"]},
                 {"target_sec_b": 5}, {"target_sec_b": 1800}, {"session_size_b": True}, {"theme": "light", "bad": 1}, {}):
        rec("PUT", "/api/settings", body)
    rec("GET", "/api/settings")
    rec("GET", "/api/home")
    for q in ("fe-30_aki-q011", "fe-05_haru-q01", "feb-sample-q02", "feb-05_haru-q01", "feb-furoku3-q01-1"):
        rec("GET", f"/api/questions/{q}")
    rec("GET", "/api/stats/categories")
    rec("GET", "/api/stats/categories?part=B")
    rec("GET", "/api/stats/time")
    rec("GET", "/api/stats/time?part=B")
    rec("GET", "/api/stats/b")
    rec("GET", "/api/history?limit=500")


def close(a, b, path, mask, out):
    """再帰比較。浮動小数は 1e-9。mask のキーは存在だけ確認"""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        if a != b or type(a) is not type(b):
            out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > 1e-9:
            out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            out.append(f"{path}: keys {sorted(set(a) ^ set(b))} differ (py={sorted(a)} js={sorted(b)})")
        for k in a.keys() & b.keys():
            if mask and k in MASK_KEYS:
                continue
            close(a[k], b[k], f"{path}.{k}", mask, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: len {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            close(x, y, f"{path}[{i}]", mask, out)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def run_parity(node="node"):
    """戻り値: (比較した応答数, 不一致のリスト)"""
    for attempt in range(2):
        tmp = Path(tempfile.mkdtemp(prefix="fe-parity-"))
        try:
            now_dt = datetime.now().replace(microsecond=0)
            db = str(tmp / "p.db")
            ids = build_fixture(db, now_dt)
            vals = [random.Random(777).random() for _ in range(1)]
            r = random.Random(777)
            vals = [r.random() for _ in range(20000)]
            app = create_app(db)
            app.config["RNG"] = ScriptRng(vals)
            client = app.test_client()
            qs, ss, at, settings = pack_format(db)
            c = connect(db)
            status_of = {r["id"]: r["status"] for r in c.execute("SELECT id,status FROM sessions")}
            c.close()
            # finish は done/abandoned のみ（active に呼ぶと Python 側の状態が変わるため phase2 で扱う）
            reqs = [r for r in read_requests(ids, sorted(status_of))
                    if not (r[1].endswith("/finish") and status_of.get(int(r[1].split("/")[3])) == "active")]
            rec1 = Recorder(client)
            for m, p in reqs:
                rec1(m, p)
            rec2 = Recorder(client)
            scenario(rec2)
            if datetime.now().date() != now_dt.date():
                continue  # 日付をまたいだ。やり直し
            data = {"questions": qs, "sessions": ss, "attempts": at, "settings": settings, "now": fmt(now_dt),
                    "rng_values": vals, "phase1": rec1.reqs, "scenario": rec2.reqs}
            (tmp / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            p = subprocess.run([node, str(HERE / "parity_node.js"), str(tmp / "data.json")],
                               capture_output=True, text=True, encoding="utf-8")
            if p.returncode != 0:
                return 0, [f"node failed: {p.stderr[-2000:]}"]
            js = json.loads(p.stdout)
            out = []
            for tag, py, jr, reqs_, mask in (("phase1", rec1.resps, js["phase1"], rec1.reqs, False),
                                              ("scenario", rec2.resps, js["scenario"], rec2.reqs, True)):
                for i, (a, b, rq) in enumerate(zip(py, jr, reqs_)):
                    close(a, b, f"{tag}[{i}] {rq['method']} {rq['path']} {json.dumps(rq['body'], ensure_ascii=False)[:80]}", mask, out)
            n = len(rec1.resps) + len(rec2.resps)
            return n, out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0, ["日付をまたいだため 2 回とも検証できませんでした"]


if __name__ == "__main__":
    n, diffs = run_parity()
    print(f"compared {n} responses, {len(diffs)} mismatches")
    for d in diffs[:60]:
        print("  ", d)
    sys.exit(1 if diffs else 0)
