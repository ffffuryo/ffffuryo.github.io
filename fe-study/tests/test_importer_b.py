"""科目B インポーター（importer/kamoku_b.py）のテスト。

    cd fe-study && python -m pytest tests/test_importer_b.py -q

PDF を使うテストは data/kamoku_b_src/ に入力 PDF がある場合だけ実行する（無ければ skip）。
IPA 解答例 PDF はキャッシュ（data/cache/）からのみ読む（ネットワークに出ない）。
書籍付録は私的使用の範囲でのみ利用できるため、このファイルには書籍の文章・画像を一切含めない
（期待値は件数・正解記号・規則だけ）。
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from importer import http_cache  # noqa: E402
from importer import kamoku_b as kb  # noqa: E402
from importer.kamoku_b_pdf import LABELS10, Header  # noqa: E402

SRC = ROOT / "data" / "kamoku_b_src"

# IPA 公表の解答例（科目B）
IPA_ANSWERS = {
    "sample": "カウカエオアカエウカアエウクアクカエエカ",
    "05_haru": "アクエエエア",
    "06_haru": "イエエイオウ",
}
EXPECTED_COUNTS = {"sample": 20, "05_haru": 6, "06_haru": 6, "furoku2": 7, "furoku3": 15, "furoku4": 12}


# ---------------------------------------------------------------- PDF 不要の単体テスト

BASE_SCHEMA = """
CREATE TABLE questions (
  id TEXT PRIMARY KEY, exam_code TEXT NOT NULL, exam_label TEXT NOT NULL, number INTEGER NOT NULL,
  category_id INTEGER NOT NULL, body TEXT NOT NULL, choices TEXT NOT NULL, answer TEXT NOT NULL,
  image_path TEXT, is_calc INTEGER NOT NULL DEFAULT 0, pool TEXT NOT NULL, source_label TEXT NOT NULL,
  source_url TEXT NOT NULL, explanation_url TEXT NOT NULL, parse_confidence REAL NOT NULL DEFAULT 1.0);
"""


def test_ensure_b_columns_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.executescript(BASE_SCHEMA)
    conn.execute("INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,pool,"
                 "source_label,source_url,explanation_url) VALUES('fe-a','01_aki','l',1,1,'b','[]','ア',"
                 "'study','s','u','e')")
    added = kb.ensure_b_columns(conn)
    assert added == ["part", "images", "group_id", "sub_label", "b_topic", "tags", "difficulty",
                     "explanation_images", "explanation_source"]
    assert kb.ensure_b_columns(conn) == []  # 2 回目は何もしない
    assert conn.execute("SELECT part FROM questions WHERE id='fe-a'").fetchone()[0] == "A"
    idx = [r[1] for r in conn.execute("PRAGMA index_list(questions)")]
    assert "idx_q_part" in idx
    conn.close()


def test_schema_sql_matches_spec():
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    for _, sql in kb.B_SCHEMA_SQL:
        assert sql + ";" in spec
    assert kb.B_INDEX_SQL + ";" in spec


def _h(text):
    return Header(lp=1, top=0, char_top=0, x0=0, num=None, line_text=text)


def test_parse_sub_answers():
    assert kb.parse_sub_answers(_h("問1：ウ〔出典〕")) == {"kind": "single", "answer": "ウ"}
    assert kb.parse_sub_answers(_h("問2（2）：イ （3）：エ")) == {"kind": "subs", "answers": {2: "イ", 3: "エ"}}
    assert kb.parse_sub_answers(_h("問3（1） x：ア （2） y：カ")) == {"kind": "subs", "answers": {1: "ア", 2: "カ"}}
    r = kb.parse_sub_answers(_h("問4 c：エ d，e：ウ，オ（順不同） f：イ"))
    assert r["kind"] == "blanks"
    assert r["answers"] == [("c", "エ", False), ("d,e", "ウ,オ", True), ("f", "イ", False)]
    assert kb.header_answer(_h("：問5 カ〔出典〕")) == "カ"


def test_slice_segs():
    from importer.kamoku_b_pdf import Seg
    segs = [Seg(1, 100, 500), Seg(2, 0, 500), Seg(3, 0, 200)]
    out = kb.slice_segs(segs, (1, 300), (3, 50))
    assert [(s.lp, s.y0, s.y1) for s in out] == [(1, 300, 500), (2, 0, 500), (3, 0, 50)]
    assert kb.slice_segs(segs, (2, 600), None) == [Seg(3, 0, 200)]


# ---------------------------------------------------------------- 実 PDF を使う回帰テスト

def _have_sources():
    if not SRC.is_dir():
        return False
    for src in kb.IPA_SOURCES:
        if not kb.find_file(SRC, src.patterns) or not kb.find_file(SRC, src.book_patterns):
            return False
    return all(kb.find_file(SRC, pats) for _, _, pats in kb.FUROKU_SOURCES)


needs_pdf = pytest.mark.skipif(not _have_sources(), reason="data/kamoku_b_src に入力 PDF が無い")


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    if not _have_sources():
        pytest.skip("入力 PDF が無い")
    tmp = tmp_path_factory.mktemp("kb")
    images = tmp / "images"
    db = tmp / "t.db"
    old = http_cache.OFFLINE
    http_cache.OFFLINE = True  # IPA 解答例はキャッシュからのみ
    try:
        # 科目A の行を 1 件入れておき、科目B の取り込みで消えないことを確かめる
        from importer.__main__ import init_database
        init_database(db)
        conn = sqlite3.connect(db)
        kb.ensure_b_columns(conn)
        conn.execute("INSERT INTO questions(id,exam_code,exam_label,number,category_id,body,choices,answer,pool,"
                     "source_label,source_url,explanation_url) VALUES('fe-01_aki-q01','01_aki','令和元年秋期',1,1,"
                     "'b','[]','ア','study','s','u','e')")
        conn.commit()
        conn.close()
        rep = kb.Report()
        items = kb.import_all(SRC, images, rep)
        res = kb.write_db(db, items, rep)
    finally:
        http_cache.OFFLINE = old
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM questions WHERE part='B' ORDER BY id")]
    n_a = conn.execute("SELECT COUNT(*) FROM questions WHERE part='A'").fetchone()[0]
    conn.close()
    return {"items": items, "rep": rep, "rows": rows, "images": images, "n_a": n_a, "res": res}


@needs_pdf
def test_counts(imported):
    by = defaultdict(int)
    for r in imported["rows"]:
        by[r["exam_code"]] += 1
    assert dict(by) == EXPECTED_COUNTS
    assert imported["n_a"] == 1  # 科目A の行は残る


@needs_pdf
def test_ipa_answers(imported):
    rows = {r["id"]: r for r in imported["rows"]}
    for code, answers in IPA_ANSWERS.items():
        for i, a in enumerate(answers, 1):
            assert rows[f"feb-{code}-q{i:02d}"]["answer"] == a, (code, i)


@needs_pdf
def test_answer_cross_check(imported):
    rep = imported["rep"]
    bad = [c for c in rep.answer_checks if not c[5]]
    assert bad == []
    # IPA 3 冊は IPA 解答例・書籍の表・書籍の解説見出しの 3 つがそろって一致している
    ipa = [c for c in rep.answer_checks if c[0] in IPA_ANSWERS]
    if any(c[2] for c in ipa):  # 解答例 PDF がキャッシュにある場合
        assert all(c[2] and c[3] and c[4] for c in ipa)


@needs_pdf
def test_pool_rules(imported):
    for r in imported["rows"]:
        if r["exam_code"] in ("05_haru", "06_haru"):
            assert r["pool"] == "mock", r["id"]
        elif r["exam_code"] == "sample":
            assert r["pool"] == "study"
        else:
            want = "mock" if re.search(r"令和\s*[5-8]\s*年", r["source_label"]) else "study"
            assert r["pool"] == want, r["id"]


@needs_pdf
def test_row_fields(imported):
    images = imported["images"]
    for r in imported["rows"]:
        assert re.fullmatch(r"feb-(sample|05_haru|06_haru|furoku[234])-q\d\d(-[0-9a-z-]+)?", r["id"]), r["id"]
        assert r["part"] == "B" and r["is_calc"] == 0 and r["explanation_url"] == ""
        assert r["category_id"] == {"pseudo": 2, "security": 11}[r["b_topic"]]
        choices = json.loads(r["choices"])
        labels = [c["label"] for c in choices]
        assert labels == list(LABELS10[:len(labels)]) and len(labels) >= 4
        assert r["answer"] in labels
        imgs = json.loads(r["images"])
        assert imgs and all((images / p).is_file() for p in imgs)
        assert all(p.startswith(f"b/{r['exam_code']}/q{r['number']:02d}") for p in imgs)
        ex = json.loads(r["explanation_images"] or "[]")
        assert ex and all((images / p).is_file() and p.startswith("b/expl/") for p in ex)
        assert r["explanation_source"]
        if r["difficulty"] is not None:
            assert r["difficulty"] in ("やさしい", "ふつう", "難しい")
        if r["tags"]:
            assert isinstance(json.loads(r["tags"]), list)
        assert r["parse_confidence"] >= kb.LOW_CONFIDENCE
    # 書籍の表から難易度・タグが入る（擬似言語は全問）
    pseudo = [r for r in imported["rows"] if r["b_topic"] == "pseudo"]
    assert len(pseudo) == 26 and all(r["difficulty"] for r in pseudo)
    assert sum(1 for r in pseudo if r["tags"]) >= 24
    topics = defaultdict(int)
    for r in imported["rows"]:
        if r["exam_code"] in IPA_ANSWERS:
            topics[(r["exam_code"], r["b_topic"])] += 1
    assert topics[("sample", "security")] == 4 and topics[("05_haru", "security")] == 1


@needs_pdf
def test_groups_contiguous(imported):
    rows = imported["rows"]
    groups = defaultdict(list)
    for r in rows:
        if r["group_id"]:
            groups[r["group_id"]].append(r)
    assert len(groups) >= 3
    for gid, members in groups.items():
        assert len(members) >= 2
        assert len({m["exam_code"] for m in members}) == 1 and len({m["number"] for m in members}) == 1
        assert len({m["sub_label"] for m in members}) == len(members) and all(m["sub_label"] for m in members)
        # id 順に並べると連続している（間に他の問題が挟まらない）
        ids = [r["id"] for r in rows]
        pos = sorted(ids.index(m["id"]) for m in members)
        assert pos == list(range(pos[0], pos[0] + len(pos)))
        # 共通問題文の画像が全設問の先頭に同じ順で入り、その後に設問固有の画像が続く
        stems = None
        for m in members:
            imgs = json.loads(m["images"])
            st = [p for p in imgs if "_stem_" in p]
            assert st and imgs[:len(st)] == st
            assert len(imgs) > len(st)
            stems = stems or st
            assert st == stems
    # 単独問題には共通画像が無い
    for r in rows:
        if not r["group_id"]:
            assert not any("_stem_" in p for p in json.loads(r["images"]))


@needs_pdf
def test_duplicates_merged(imported):
    rep = imported["rep"]
    targets = {m[1] for m in rep.merged}
    # 付録2 の FE 公開問題・サンプル問題由来の問は IPA 版に統合されている
    assert {"feb-05_haru-q06", "feb-sample-q17", "feb-sample-q18", "feb-sample-q19",
            "feb-sample-q20"} <= targets
    ids = {r["id"] for r in imported["rows"]}
    assert not {"feb-furoku2-q04", "feb-furoku2-q05", "feb-furoku2-q08"} & ids


# ---------------------------------------------------------------- 画像と問の対応（importer/audit_b.py）

def test_resolve_header_numbers_overlay():
    """付録4 の重ね文字（出典の問49 と付録の問1 が重ねて描かれ、数字が混ざって取れる）を表の番号に解決する。"""
    orig = {48 + k: k for k in range(1, 13)}  # 出典の問49..60 -> 付録の問1..12
    hs = [Header(1, 0, 0, 0, None, "", "419"), Header(2, 0, 0, 0, None, "", "5191"),
          Header(3, 0, 0, 0, 56, "", "56"), Header(4, 0, 0, 0, 12, "", "12"), Header(5, 0, 0, 0, None, "", "999")]
    assert kb.resolve_header_numbers(hs, list(range(1, 13)), orig) == [1, 11, 8, 12, None]
    # 番号が読めないものがあれば出現順で割り当て、警告する
    rep = kb.Report()
    rep.warn = rep.warnings.append
    got = kb.numbered_headers(hs[:2] + [Header(6, 0, 0, 0, None, "", "")], [1, 2, 3], orig, rep, "t")
    assert [n for n, _ in got] == [1, 2, 3] and rep.warnings


def test_audit_heading_answer():
    from importer.audit_b import expected_answer_from_heading as ea
    assert ea("問1：カ", None) == "カ"
    assert ea("：問17カ", None) == "カ"
    assert ea("問：20カ", None) == "カ"
    assert ea("問1イやさしい", None) == "イ"
    assert ea("問問4１9：イ", None) == "イ"
    assert ea("問2（2）：イ （3）：エ", "設問3(3)") == "エ"
    assert ea("問9 c：エ d，e：ウ，オ（順不同） f：イ", "設問2(1) f") == "イ"
    assert ea("問9 c：エ d，e：ウ，オ（順不同） f：イ", "設問2(1) c") == "エ"


@pytest.fixture(scope="module")
def audited(imported):
    from importer import audit_b
    aud = audit_b.Auditor(SRC, imported["images"])
    yield aud, {r.id: r for r in aud.run(imported["rows"])}
    aud.close()


@needs_pdf
def test_manifest_covers_all_images(imported, audited):
    aud, _ = audited
    for r in imported["rows"]:
        for p in json.loads(r["images"]) + json.loads(r["explanation_images"] or "[]"):
            assert p in aud.manifest, p


@needs_pdf
def test_audit_no_ng(audited):
    _, res = audited
    ng = {k: v.reasons for k, v in res.items() if not v.ok}
    assert ng == {}


@needs_pdf
def test_explanation_matches_question(imported, audited):
    """全 B 問題で: 解説の先頭画像は期待する問の見出しから始まり、その見出しの正解記号が answer と一致し、
    解説画像に他の問の見出しが入っていない。問題画像も先頭が同じ問番号で、他の問が入っていない。"""
    from importer.audit_b import expected_answer_from_heading
    aud, _ = audited
    for r in imported["rows"]:
        ex = json.loads(r["explanation_images"] or "[]")
        regs = [aud.manifest[p] for p in ex]
        cands = aud.expected_expl(r)
        assert ex and cands, r["id"]
        assert len({m["pdf"] for m in regs}) == 1
        pdf = regs[0]["pdf"]
        want = [n for f, n in cands if f == pdf]
        assert want, (r["id"], pdf, cands)
        s = aud.src(pdf)
        heads = [h for m in regs for h in s.heads_in(m["lp"], m["y0"], m["y1"])]
        assert heads and heads[0].n == want[0], (r["id"], [h.n for h in heads])
        assert {h.n for h in heads} == {want[0]}, (r["id"], [h.n for h in heads])
        first = s.heads_in(regs[0]["lp"], regs[0]["y0"], regs[0]["y1"])
        assert first and first[0] is heads[0], r["id"]
        assert expected_answer_from_heading(heads[0].text, r["sub_label"]) == r["answer"], r["id"]
        if r["exam_code"] in IPA_ANSWERS:  # IPA の問は同じ回の書籍解説の同じ番号
            assert pdf == aud.book_pdf[r["exam_code"]] and want[0] == r["number"], r["id"]
        # 問題画像
        q = [aud.manifest[p] for p in json.loads(r["images"])]
        qs = aud.src(q[0]["pdf"])
        qh = [h for m in q for h in qs.heads_in(m["lp"], m["y0"], m["y1"])]
        assert qh and qh[0].n == r["number"] and {h.n for h in qh} == {r["number"]}, r["id"]


@needs_pdf
def test_audit_detects_swapped_explanations(imported, audited):
    """監査が対応ずれを検出できること（2 問の解説画像を入れ替えると両方 NG）。"""
    aud, _ = audited
    rows = {r["id"]: dict(r) for r in imported["rows"]}
    a, b = rows["feb-sample-q05"], rows["feb-sample-q06"]
    a["explanation_images"], b["explanation_images"] = b["explanation_images"], a["explanation_images"]
    c = rows["feb-sample-q10"]
    c["explanation_images"] = json.dumps(json.loads(c["explanation_images"])[:-1])  # 末尾を欠く
    res = {x.id: x for x in aud.run([a, b, c])}
    assert not res["feb-sample-q05"].ok and not res["feb-sample-q06"].ok
    assert any("見出し" in m for m in res["feb-sample-q05"].reasons)
    assert not res["feb-sample-q10"].ok and any("欠けている" in m for m in res["feb-sample-q10"].reasons)
