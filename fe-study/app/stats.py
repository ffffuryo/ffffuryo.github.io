import statistics
from datetime import date, timedelta

from .categories import FIELD_NAMES
from .db import get_settings

# 問題ごとの最新の回答（未回答 chosen IS NULL は除く）
LATEST = (
    "SELECT * FROM attempts WHERE id IN "
    "(SELECT MAX(id) FROM attempts WHERE chosen IS NOT NULL GROUP BY question_id)"
)
VALID = "chosen IS NOT NULL"


def _today(conn):
    return conn.execute("SELECT date('now','localtime')").fetchone()[0]


def today_summary(conn):
    r = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(is_correct),0) c, COALESCE(SUM(time_ms),0) t "
        f"FROM attempts WHERE {VALID} AND date(answered_at)=date('now','localtime')"
    ).fetchone()
    return {"answered": r["n"], "correct": r["c"], "time_ms": r["t"]}


def streak_days(conn):
    days = {r[0] for r in conn.execute(f"SELECT DISTINCT date(answered_at) FROM attempts WHERE {VALID}")}
    d = date.fromisoformat(_today(conn))
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def category_stats(conn):
    target_ms = get_settings(conn)["target_sec"] * 1000
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    total = {r[0]: r[1] for r in conn.execute(
        "SELECT category_id, COUNT(*) FROM questions WHERE pool='study' GROUP BY category_id")}
    tot = {r["category_id"]: r for r in conn.execute(
        f"SELECT q.category_id, COUNT(*) attempts, SUM(a.is_correct) correct, AVG(a.time_ms) avg_ms "
        f"FROM attempts a JOIN questions q ON q.id=a.question_id "
        f"WHERE q.pool='study' AND a.{VALID} GROUP BY q.category_id")}
    latest = {r["category_id"]: r for r in conn.execute(
        f"SELECT q.category_id, COUNT(*) seen, SUM(l.is_correct) ok, "
        f"SUM(CASE WHEN l.is_correct=1 AND l.time_ms<=? THEN 1 ELSE 0 END) mastered "
        f"FROM ({LATEST}) l JOIN questions q ON q.id=l.question_id "
        f"WHERE q.pool='study' GROUP BY q.category_id", (target_ms,))}
    fields = {}
    for c in cats:
        t, l = tot.get(c["id"]), latest.get(c["id"])
        seen = l["seen"] if l else 0
        item = {
            "id": c["id"], "name": c["name"], "major_no": c["major_no"], "major_name": c["major_name"],
            "total": total.get(c["id"], 0), "seen": seen,
            "attempts": t["attempts"] if t else 0, "correct": (t["correct"] or 0) if t else 0,
            "accuracy": round(l["ok"] / seen, 4) if seen else None,
            "avg_time_ms": int(t["avg_ms"]) if t else None,
            "mastered": (l["mastered"] or 0) if l else 0,
        }
        fields.setdefault(c["field"], []).append(item)
    return {"fields": [{"field": f, "name": FIELD_NAMES[f], "categories": fields[f]}
                       for f in ("te", "ma", "st") if f in fields]}


def weak_categories(conn, limit=3):
    out = []
    for f in category_stats(conn)["fields"]:
        for c in f["categories"]:
            if c["accuracy"] is not None:
                out.append({"id": c["id"], "name": c["name"], "accuracy": c["accuracy"]})
    out.sort(key=lambda c: (c["accuracy"], c["id"]))
    return out[:limit]


def _avg(v):
    return int(sum(v) / len(v)) if v else None


def time_stats(conn):
    target_ms = get_settings(conn)["target_sec"] * 1000
    rows = conn.execute(
        f"SELECT a.question_id, a.is_correct, a.time_ms, date(a.answered_at) d, q.category_id, "
        f"c.name cname, q.is_calc, q.exam_label, q.number FROM attempts a "
        f"JOIN questions q ON q.id=a.question_id JOIN categories c ON c.id=q.category_id "
        f"WHERE a.{VALID} ORDER BY a.id").fetchall()
    times = [r["time_ms"] for r in rows]
    overall = {
        "avg_ms": _avg(times),
        "median_ms": int(statistics.median(times)) if times else None,
        "over_target_rate": round(sum(t > target_ms for t in times) / len(times), 4) if times else None,
    }
    bycat = {}
    for r in rows:
        bycat.setdefault(r["category_id"], []).append(r)
    by_category = [{"id": cid, "name": rs[0]["cname"], "avg_ms": _avg([x["time_ms"] for x in rs]),
                    "median_ms": int(statistics.median([x["time_ms"] for x in rs])), "n": len(rs)}
                   for cid, rs in sorted(bycat.items())]

    def grp(rs):
        return {"avg_ms": _avg([x["time_ms"] for x in rs]),
                "accuracy": round(sum(x["is_correct"] for x in rs) / len(rs), 4) if rs else None,
                "n": len(rs)}

    calc = grp([r for r in rows if r["is_calc"]])
    noncalc = grp([r for r in rows if not r["is_calc"]])
    since = (date.fromisoformat(_today(conn)) - timedelta(days=29)).isoformat()
    byday = {}
    for r in rows:
        if r["d"] >= since:
            byday.setdefault(r["d"], []).append(r)
    daily = [{"date": d, "n": len(rs), "avg_ms": _avg([x["time_ms"] for x in rs]),
              "accuracy": round(sum(x["is_correct"] for x in rs) / len(rs), 4)}
             for d, rs in sorted(byday.items())]
    slowest = [{"question_id": r["question_id"], "label": f"{r['exam_label']} 問{r['number']}",
                "category_name": r["cname"], "time_ms": r["time_ms"], "is_correct": bool(r["is_correct"])}
               for r in sorted(rows, key=lambda r: -r["time_ms"])[:10]]
    buckets = [("〜30秒", 0, 30000), ("30〜60秒", 30000, 60000), ("60〜90秒", 60000, 90000),
               ("90〜120秒", 90000, 120000), ("120秒〜", 120000, None)]
    speed = []
    for name, lo, hi in buckets:
        rs = [r for r in rows if r["time_ms"] >= lo and (hi is None or r["time_ms"] < hi)]
        speed.append({"bucket": name, "n": len(rs),
                      "accuracy": round(sum(r["is_correct"] for r in rs) / len(rs), 4) if rs else None})
    return {"overall": overall, "by_category": by_category,
            "calc_vs_noncalc": {"calc": calc, "noncalc": noncalc},
            "daily": daily, "slowest": slowest, "speed_accuracy": speed}
