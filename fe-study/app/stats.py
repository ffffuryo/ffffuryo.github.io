import json
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


def category_stats(conn, part="A"):
    st = get_settings(conn)
    target_ms = (st["target_sec_b"] if part == "B" else st["target_sec"]) * 1000
    cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    total = {r[0]: r[1] for r in conn.execute(
        "SELECT category_id, COUNT(*) FROM questions WHERE pool='study' AND part=? GROUP BY category_id",
        (part,))}
    tot = {r["category_id"]: r for r in conn.execute(
        f"SELECT q.category_id, COUNT(*) attempts, SUM(a.is_correct) correct, AVG(a.time_ms) avg_ms "
        f"FROM attempts a JOIN questions q ON q.id=a.question_id "
        f"WHERE q.pool='study' AND q.part=? AND a.{VALID} GROUP BY q.category_id", (part,))}
    latest = {r["category_id"]: r for r in conn.execute(
        f"SELECT q.category_id, COUNT(*) seen, SUM(l.is_correct) ok, "
        f"SUM(CASE WHEN l.is_correct=1 AND l.time_ms<=? THEN 1 ELSE 0 END) mastered "
        f"FROM ({LATEST}) l JOIN questions q ON q.id=l.question_id "
        f"WHERE q.pool='study' AND q.part=? GROUP BY q.category_id", (target_ms, part))}
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


def time_stats(conn, part="all"):
    st = get_settings(conn)
    target = {"A": st["target_sec"] * 1000, "B": st["target_sec_b"] * 1000}
    pf, pargs = ("", ()) if part == "all" else (" AND q.part=?", (part,))
    rows = conn.execute(
        f"SELECT a.question_id, a.is_correct, a.time_ms, date(a.answered_at) d, q.category_id, "
        f"c.name cname, q.is_calc, q.exam_label, q.number, q.part, q.sub_label FROM attempts a "
        f"JOIN questions q ON q.id=a.question_id JOIN categories c ON c.id=q.category_id "
        f"WHERE a.{VALID}{pf} ORDER BY a.id", pargs).fetchall()
    times = [r["time_ms"] for r in rows]
    overall = {
        "avg_ms": _avg(times),
        "median_ms": int(statistics.median(times)) if times else None,
        "over_target_rate": round(sum(r["time_ms"] > target[r["part"]] for r in rows) / len(rows), 4) if rows else None,
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
    slowest = [{"question_id": r["question_id"], "label": f"{r['exam_label']} 問{r['number']}" + (f" {r['sub_label']}" if r["sub_label"] else ""),
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


B_TOPIC_NAMES = {"pseudo": "擬似言語", "security": "情報セキュリティ"}
DIFFICULTIES = ("やさしい", "ふつう", "難しい")


def b_stats(conn):
    """科目B（study プール）のテーマ別・タグ別・難易度別。正答率は最新回答ベース、平均時間は延べ。"""
    rows = conn.execute(
        f"SELECT q.id, q.b_topic, q.tags, q.difficulty, l.is_correct, "
        f"(SELECT COUNT(*) FROM attempts a WHERE a.question_id=q.id AND a.{VALID}) n_att, "
        f"(SELECT COALESCE(SUM(time_ms),0) FROM attempts a WHERE a.question_id=q.id AND a.{VALID}) sum_ms "
        f"FROM questions q LEFT JOIN ({LATEST}) l ON l.question_id=q.id "
        f"WHERE q.pool='study' AND q.part='B'").fetchall()

    def agg(rs):
        seen = [r for r in rs if r["is_correct"] is not None]
        n_att = sum(r["n_att"] for r in rs)
        return {"total": len(rs), "seen": len(seen),
                "accuracy": round(sum(r["is_correct"] for r in seen) / len(seen), 4) if seen else None,
                "avg_time_ms": int(sum(r["sum_ms"] for r in rs) / n_att) if n_att else None}

    by_topic, by_tag, by_diff = {}, {}, {}
    for r in rows:
        by_topic.setdefault(r["b_topic"], []).append(r)
        if r["difficulty"]:
            by_diff.setdefault(r["difficulty"], []).append(r)
        try:
            tags = json.loads(r["tags"]) if r["tags"] else []
        except ValueError:
            tags = []
        for t in dict.fromkeys(x for x in tags if isinstance(x, str)):
            by_tag.setdefault(t, []).append(r)
    topics = [{"b_topic": k, "name": B_TOPIC_NAMES.get(k, k), **agg(by_topic.get(k, []))}
              for k in list(B_TOPIC_NAMES) + [k for k in by_topic if k not in B_TOPIC_NAMES and k]]
    tags = [{"tag": t, **agg(rs)} for t, rs in
            sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    order = list(DIFFICULTIES) + sorted(k for k in by_diff if k not in DIFFICULTIES)
    diff = [{"difficulty": k, **agg(by_diff[k])} for k in order if k in by_diff]
    return {"topics": topics, "tags": tags, "difficulty": diff}
