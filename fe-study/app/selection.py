import random

from .db import get_settings
from .stats import LATEST


def _filters(include_calc, category_ids):
    sql, args = "", []
    if not include_calc:
        sql += " AND q.is_calc=0"
    if category_ids:
        sql += f" AND q.category_id IN ({','.join('?' * len(category_ids))})"
        args += list(category_ids)
    return sql, args


def _take_weighted(items, weight, n, rng):
    keyed = sorted(items, key=lambda it: rng.random() ** (1.0 / weight(it)), reverse=True)
    return keyed[:n]


def pick_study(conn, size, include_calc=True, category_ids=None, rng=None, target_sec=None):
    rng = rng or random.Random()
    if target_sec is None:
        target_sec = get_settings(conn)["target_sec"]
    target_ms = target_sec * 1000
    f, args = _filters(include_calc, category_ids)
    rows = conn.execute(
        f"SELECT q.id, q.category_id, l.is_correct, l.time_ms, l.answered_at FROM questions q "
        f"LEFT JOIN ({LATEST}) l ON l.question_id=q.id WHERE q.pool='study'{f}", args).fetchall()
    today = conn.execute("SELECT date('now','localtime')").fetchone()[0]
    wrong, unseen, slow, mastered = [], [], [], []
    for r in rows:
        if r["is_correct"] is None:
            unseen.append(r)
        elif not r["is_correct"]:
            wrong.append(r)
        elif r["time_ms"] > target_ms:
            slow.append(r)
        else:
            mastered.append(r)
    # 分野別正答率（最新回答ベース）。低いほど重い
    stat = {}
    for r in rows:
        if r["is_correct"] is not None:
            s = stat.setdefault(r["category_id"], [0, 0])
            s[0] += r["is_correct"]
            s[1] += 1
    def weight(r):
        s = stat.get(r["category_id"])
        acc = s[0] / s[1] if s else 0.5
        return 1.0 + (1.0 - acc)

    n = min(size, len(rows))
    quota = {"wrong": round(size * 0.3), "unseen": round(size * 0.5), "slow": round(size * 0.1)}
    quota["mastered"] = max(0, size - sum(quota.values()))

    # 同日に出たものは後回し
    rng.shuffle(wrong)
    wrong.sort(key=lambda r: (r["answered_at"] or "")[:10] == today)
    rng.shuffle(slow)
    mastered.sort(key=lambda r: r["answered_at"] or "")
    unseen_order = _take_weighted(unseen, weight, len(unseen), rng)
    pools = {"wrong": wrong, "unseen": unseen_order, "slow": slow, "mastered": mastered}

    chosen, used = [], set()
    for name in ("wrong", "unseen", "slow", "mastered"):
        for r in pools[name][:quota[name]]:
            chosen.append(r["id"]); used.add(r["id"])
    for name in ("wrong", "unseen", "slow", "mastered"):
        for r in pools[name]:
            if len(chosen) >= n:
                break
            if r["id"] not in used:
                chosen.append(r["id"]); used.add(r["id"])
    chosen = chosen[:n]
    rng.shuffle(chosen)
    return chosen


def review_ids(conn, category_ids=None):
    f, args = _filters(True, category_ids)
    return [r[0] for r in conn.execute(
        f"SELECT q.id FROM questions q JOIN ({LATEST}) l ON l.question_id=q.id "
        f"WHERE q.pool='study' AND l.is_correct=0{f} ORDER BY l.answered_at DESC, l.id DESC", args)]


def pick_review(conn, size=None, category_ids=None, rng=None):
    ids = review_ids(conn, category_ids)
    if rng:
        rng.shuffle(ids)
    return ids[:size] if size else ids


def pick_mock(conn, exam_code=None, size=20, rng=None):
    rng = rng or random.Random()
    if exam_code:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM questions WHERE pool='mock' AND exam_code=? ORDER BY number", (exam_code,))]
        return ids
    ids = [r[0] for r in conn.execute("SELECT id FROM questions WHERE pool='mock'")]
    rng.shuffle(ids)
    return ids[:size]


def mock_time_limit(n):
    return n * 90
