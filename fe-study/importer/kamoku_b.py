"""FE 科目B 問題インポーター（docs/SPEC.md 6章）。

    cd fe-study
    python -m importer.kamoku_b                       # data/kamoku_b_src の PDF を data/fe.db に UPSERT
    python -m importer.kamoku_b --src DIR --db PATH   # 入力ディレクトリ・DB を指定
    python -m importer.kamoku_b --offline             # IPA 解答例 PDF をキャッシュからのみ読む

入力（data/kamoku_b_src/、git 管理外）:
  IPA 問題冊子 3 冊（サンプル20問・令和5年6問・令和6年6問）と、翔泳社『出るとこだけ！基本情報技術者［科目B］
  第4版』の付録 PDF（上記の解説 3 冊、付録2/3/4 の情報セキュリティ問題＋解説）。

出力:
  - questions 行（part='B'。id は feb-{src}-q{NN}[-{sub}]）。既存の科目A の行には触れない。
  - 問題画像 data/images/b/{exam_code}/q{NN}[-sub]_{k}.png（共通問題文は q{NN}_stem_{k}.png）
  - 解説画像 data/images/b/expl/{exam_code}/q{NN}_{k}.png（書籍付録の該当ページの切り抜き。ローカル利用のみ）

正解: IPA 3 冊は IPA 解答例 PDF（www.ipa.go.jp、http_cache 経由で 1 秒以上間隔・キャッシュ）と書籍の表を
照合する。付録の問題は付録の表／解説見出しの「問N：ア」から取る。
重複: 付録の問題の出典が IPA 3 冊のいずれかと同じ（または本文がほぼ同一）なら付録版は作らず、
IPA 版（または先に取り込んだ付録版）に解説画像だけを紐付ける。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # fe-study/
sys.path.insert(0, str(ROOT))

from . import http_cache  # noqa: E402
from .kamoku_b_pdf import (LABELS10, Doc, Header, Seg, nfkc, parse_choices,  # noqa: E402
                           save_segs)

DEFAULT_SRC = ROOT / "data" / "kamoku_b_src"
DEFAULT_DB = ROOT / "data" / "fe.db"
IMAGES_DIR = ROOT / "data" / "images"
LOW_CONFIDENCE = 0.7

SCALE_Q = 2.4      # 問題画像の描画倍率（IPA 本文 10pt -> 約 24px/字。スマホ幅でも擬似言語が読める）
SCALE_EXPL = 2.0   # 解説画像

BOOK_TITLE = "『出るとこだけ！基本情報技術者［科目B］第4版』"
BOOK_URL = "https://www.shoeisha.co.jp/book/detail/9784798183466"
TOPIC_CATEGORY = {"pseudo": 2, "security": 11}

# SPEC 6.1 と完全一致させること（app/db.py の init_db がまだ無い DB 向けの冪等ヘルパーで使う）
B_SCHEMA_SQL = [
    ("part", "ALTER TABLE questions ADD COLUMN part TEXT NOT NULL DEFAULT 'A'"),
    ("images", "ALTER TABLE questions ADD COLUMN images TEXT"),
    ("group_id", "ALTER TABLE questions ADD COLUMN group_id TEXT"),
    ("sub_label", "ALTER TABLE questions ADD COLUMN sub_label TEXT"),
    ("b_topic", "ALTER TABLE questions ADD COLUMN b_topic TEXT"),
    ("tags", "ALTER TABLE questions ADD COLUMN tags TEXT"),
    ("difficulty", "ALTER TABLE questions ADD COLUMN difficulty TEXT"),
    ("explanation_images", "ALTER TABLE questions ADD COLUMN explanation_images TEXT"),
    ("explanation_source", "ALTER TABLE questions ADD COLUMN explanation_source TEXT"),
]
B_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_q_part ON questions(part, pool)"


@dataclass
class IpaSource:
    code: str
    label: str
    pool: str
    patterns: tuple          # 問題冊子 PDF のファイル名パターン（data/kamoku_b_src 内）
    qs_url: str
    ans_url: str
    src_prefix: str          # 出典表示
    book_patterns: tuple     # 書籍の解説 PDF
    book_name: str
    n_pseudo: int            # 書籍の表が読めないときの擬似言語問題数（問1〜n）


IPA_SOURCES = [
    IpaSource("sample", "科目Bサンプル問題", "study", ("fe_kamoku_b_set_sample_qs*.pdf",),
              "https://www.ipa.go.jp/shiken/syllabus/henkou/2022/ssf7ph000000h5tb-att/fe_kamoku_b_set_sample_qs.pdf",
              "https://www.ipa.go.jp/shiken/syllabus/henkou/2022/ssf7ph000000h5tb-att/fe_kamoku_b_set_sample_ans.pdf",
              "基本情報技術者試験 科目B サンプル問題", ("FE_B-R04sample*.pdf",), "令和4年サンプル問題 科目B 解説", 16),
    IpaSource("05_haru", "令和5年度", "mock", ("2023r05_fe_kamoku_b_qs*.pdf",),
              "https://www.ipa.go.jp/shiken/mondai-kaiotu/sg_fe/koukai/t6hhco0000003zx0-att/2023r05_fe_kamoku_b_qs.pdf",
              "https://www.ipa.go.jp/shiken/mondai-kaiotu/sg_fe/koukai/t6hhco0000003zx0-att/2023r05_fe_kamoku_b_ans.pdf",
              "令和5年度 基本情報技術者試験 科目B 公開問題", ("FE_B-R05sample*.pdf",), "令和5年公開問題 科目B 解説", 5),
    IpaSource("06_haru", "令和6年度", "mock", ("2024r06_fe_kamoku_b_qs*.pdf",),
              "https://www.ipa.go.jp/shiken/mondai-kaiotu/sg_fe/koukai/eid2eo0000007g1d-att/2024r06_fe_kamoku_b_qs.pdf",
              "https://www.ipa.go.jp/shiken/mondai-kaiotu/sg_fe/koukai/eid2eo0000007g1d-att/2024r06_fe_kamoku_b_ans.pdf",
              "令和6年度 基本情報技術者試験 科目B 公開問題", ("FE_B-R06sample*.pdf",), "令和6年公開問題 科目B 解説", 5),
]

FUROKU_SOURCES = [  # (exam_code, 付録番号, ファイル名パターン)
    ("furoku2", 2, ("FE_B-4th-furoku2*.pdf",)),
    ("furoku3", 3, ("FE_B-4th-furoku3*.pdf",)),
    ("furoku4", 4, ("FE_B-4th-furoku4*.pdf",)),
]

# 付録の出典 -> 取り込み済み IPA 問題（同一問題）
RE_SRC_IPA = [
    (re.compile(r"基本情報技術者試験\s*令和4年サンプル問題\s*科目B\s*問(\d+)"), "sample"),
    (re.compile(r"基本情報技術者試験\s*令和5年公開問題\s*科目B\s*問(\d+)"), "05_haru"),
    (re.compile(r"基本情報技術者試験\s*令和6年公開問題\s*科目B\s*問(\d+)"), "06_haru"),
]


def find_file(src: Path, patterns) -> Path | None:
    for pat in patterns:
        hits = sorted(src.glob(pat))
        if hits:
            return hits[-1]
    return None


# --------------------------------------------------------------------------
# 問題レコード
# --------------------------------------------------------------------------

@dataclass
class Item:
    id: str
    exam_code: str
    exam_label: str
    number: int
    pool: str
    b_topic: str
    answer: str
    choices: list
    images: list
    body: str
    source_label: str
    source_url: str
    sub_label: str | None = None
    group_id: str | None = None
    tags: list | None = None
    difficulty: str | None = None
    explanation_images: list | None = None
    explanation_source: str | None = None
    parse_confidence: float = 1.0
    notes: list = field(default_factory=list)
    origin: str = ""          # 'ipa' | 'furoku'

    def row(self) -> dict:
        return {
            "id": self.id, "exam_code": self.exam_code, "exam_label": self.exam_label,
            "number": self.number, "category_id": TOPIC_CATEGORY[self.b_topic], "body": self.body,
            "choices": json.dumps(self.choices, ensure_ascii=False), "answer": self.answer,
            "image_path": None, "is_calc": 0, "pool": self.pool, "source_label": self.source_label,
            "source_url": self.source_url, "explanation_url": "",
            "parse_confidence": round(max(0.0, min(1.0, self.parse_confidence)), 2),
            "part": "B", "images": json.dumps(self.images, ensure_ascii=False),
            "group_id": self.group_id, "sub_label": self.sub_label, "b_topic": self.b_topic,
            "tags": json.dumps(self.tags, ensure_ascii=False) if self.tags else None,
            "difficulty": self.difficulty,
            "explanation_images": json.dumps(self.explanation_images, ensure_ascii=False)
            if self.explanation_images else None,
            "explanation_source": self.explanation_source if self.explanation_images else None,
        }


@dataclass
class Report:
    warnings: list = field(default_factory=list)
    answer_checks: list = field(default_factory=list)   # (src, 問, ipa, book, expl, ok)
    merged: list = field(default_factory=list)          # (付録の問, 統合先 id, 理由)
    skipped: list = field(default_factory=list)
    similar: list = field(default_factory=list)         # 統合しなかった類似問題

    def warn(self, msg):
        self.warnings.append(msg)
        print("     WARN", msg, flush=True)


def norm_text(s: str) -> str:
    s = nfkc(s)
    s = re.sub(r"^\s*問\s*\d+", "", s)
    return re.sub(r"[^0-9A-Za-z぀-ヿ一-鿿]", "", s)


def similar(a: str, b: str, n: int = 160) -> float:
    a, b = norm_text(a)[:n], norm_text(b)[:n]
    if len(a) < 30 or len(b) < 30:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
        return 0.0
    return sm.ratio()


def text_of(doc: Doc, segs: list[Seg]) -> str:
    return "\n".join(l.text for l in doc.region_lines(segs))


def slice_segs(segs: list[Seg], start: tuple | None, end: tuple | None) -> list[Seg]:
    out = []
    for s in segs:
        y0, y1 = s.y0, s.y1
        if start and (s.lp < start[0] or (s.lp == start[0] and y1 <= start[1])):
            continue
        if end and (s.lp > end[0] or (s.lp == end[0] and y0 >= end[1])):
            continue
        if start and s.lp == start[0]:
            y0 = max(y0, start[1])
        if end and s.lp == end[0]:
            y1 = min(y1, end[1])
        if y1 - y0 > 4:
            out.append(Seg(s.lp, y0, y1))
    return out


# --------------------------------------------------------------------------
# 書籍の表
# --------------------------------------------------------------------------

def _find_word(chars: list, word: str):
    """x 順に並んだ文字列から word が連続して現れる位置の先頭文字を返す。"""
    s = "".join(c["text"] for c in chars)
    i = s.find(word)
    return (chars[i], chars[i + len(word) - 1]) if i >= 0 else None


def _box_x1(P, ch) -> float | None:
    cy = (ch["top"] + ch["bottom"]) / 2
    xs = [s["x1"] for s in P.shapes if s["x0"] <= ch["x0"] and s["x1"] >= ch["x1"]
          and s["top"] <= cy <= s["bottom"] and s["height"] < 25]
    return max(xs) if xs else None


def parse_answer_book_table(doc: Doc, rep: Report, tag: str) -> dict:
    """書籍の解説 PDF 1 ページ目の表: {問番号: {answer, page, topic, difficulty, tags}}"""
    P = doc.lp(1)
    res = {}
    rows = []
    for l in P.lines:
        m = re.match(r"^問\s*(\d+)\s*([アイウエオカキクケコ])\s*p\.?\s*(\d+)", nfkc(l.text))
        if m:
            rows.append((l, int(m.group(1)), m.group(2), int(m.group(3))))
    hdr_p = next((l for l in P.lines if "擬似言語" in l.text and l.x0 < P.x0 + 100), None)
    hdr_s = next((l for l in P.lines if "情報セキュリティ" in l.text and l.x0 < P.x0 + 100), None)
    if not rows or hdr_p is None:
        rep.warn(f"{tag}: 書籍の表が読めない")
        return res
    hchars = sorted([c for c in P.chars if abs(c["top"] - hdr_p.top) < 4], key=lambda c: c["x0"])
    cols = []
    for word in ("トレース", "データ構造", "オブジェクト指向", "再帰"):
        f = _find_word(hchars, word)
        if f is not None:
            cols.append((f[0]["x0"], f[1]["x1"], word))
    cols.sort()
    trace_x0 = cols[0][0] if cols else None
    box_p = _box_x1(P, next(c for c in hchars if c["text"] == "擬"))
    box_s = None
    if hdr_s is not None:
        sch = next((c for c in hdr_s.chars if c["text"] == "情"), None)
        box_s = _box_x1(P, sch) if sch else None
    S = 3.0
    img = np.asarray(doc.render(P.pi, S)).astype(float)
    for l, n, ans, page in rows:
        topic = "security" if (hdr_s is not None and l.top > hdr_s.top) else "pseudo"
        # 難易度: 楕円マーカーの塗り（白=やさしい、灰=ふつう、黒=難しい）を描画して判定
        diff = None
        x0 = box_p
        x1 = trace_x0 - 1 if topic == "pseudo" else (box_s if (box_s and box_p and box_s > box_p + 10) else None)
        if x0 and x1 and x1 > x0 + 8:
            reg = img[int((l.top - 1) * S):int((l.bottom + 1) * S), int(x0 * S):int(x1 * S)]
            if reg.size:
                dark = float((reg < 80).mean())
                mid = float(((reg >= 80) & (reg < 230)).mean())
                diff = "難しい" if dark > 0.25 else ("ふつう" if mid > 0.3 else "やさしい")
        tags = []
        if topic == "pseudo" and cols:
            cy = (l.top + l.bottom) / 2
            rch = sorted([c for c in P.chars if abs((c["top"] + c["bottom"]) / 2 - cy) < 4.5],
                         key=lambda c: c["x0"])
            for i, (hx0, hx1, word) in enumerate(cols):
                # 列の範囲: 左隣の見出しの右端〜右隣の見出しの左端（データ構造のセルは左寄せのため）
                cx0 = cols[i - 1][1] + 1 if i > 0 else hx0 - 6
                cx1 = cols[i + 1][0] - 1 if i + 1 < len(cols) else hx1 + 25
                txt = "".join(c["text"] for c in rch if cx0 <= c["x0"] < cx1).strip()
                if word == "データ構造":
                    tags += [t.strip() for t in re.split(r"[，,、]", txt) if t.strip() and t.strip() != "○"]
                elif "○" in txt:
                    tags.append(word)
            order = {"トレース": 0, "オブジェクト指向": 2, "再帰": 3}
            tags.sort(key=lambda t: order.get(t, 1))
        res[n] = {"answer": ans, "page": page, "topic": topic, "difficulty": diff, "tags": tags}
    return res


def parse_furoku_table(doc: Doc, rep: Report, tag: str) -> dict:
    """付録 1 ページ目の表: {問番号: {answer|None, qpage, epage, source}}"""
    res = {}
    for l in doc.lp(1).lines:
        m = re.match(r"^問\s*(\d+)\s+(?:([アイウエオカキクケコ])\s+)?p\.?\s*(\d+)\s+p\.?\s*(\d+)\s*〔(.+?)〕",
                     nfkc(l.text))
        if m:
            res[int(m.group(1))] = {"answer": m.group(2), "qpage": int(m.group(3)), "epage": int(m.group(4)),
                                    "source": m.group(5).strip()}
    if not res:
        rep.warn(f"{tag}: 付録の表が読めない")
    return res


def header_answer(h: Header) -> str | None:
    t = nfkc(h.line_text).replace(" ", "")
    t = re.sub(r"問\d+", "", t, count=1)
    m = re.search(r":([アイウエオカキクケコ])", t)
    return m.group(1) if m else None


def parse_sub_answers(h: Header) -> dict:
    """付録3 の解説見出し「問2（2）：イ （3）：イ」「問9 c：エ d，e：ウ，オ（順不同） f：イ」を解析。"""
    t = nfkc(h.line_text).replace(" ", "").replace("　", "")
    t = re.sub(r"問\d+", "", t, count=1).split("〔")[0]
    subs = re.findall(r"\((\d)\)(?:[a-z]\d?)?:([アイウエオカキクケコ])", t)
    if subs:
        return {"kind": "subs", "answers": {int(k): a for k, a in subs}}
    blanks = re.findall(r"((?:[a-z]\d?,?)+):([アイウエオカキクケコ](?:,[アイウエオカキクケコ])*)(\(順不同\))?", t)
    if blanks:
        return {"kind": "blanks", "answers": [(b.rstrip(","), a, bool(u)) for b, a, u in blanks]}
    m = re.search(r":([アイウエオカキクケコ])", t)
    return {"kind": "single", "answer": m.group(1) if m else None}


def explanation_regions(doc: Doc, first_lp: int, expected: list[int], rep: Report, tag: str) -> dict:
    """解説部の「問N」見出しから {問番号: (Header, [Seg])}。番号は読めれば読んだ値、読めなければ出現順。"""
    hs = doc.book_headers(range(first_lp, len(doc) + 1))
    if len(hs) != len(expected):
        rep.warn(f"{tag}: 解説見出し {len(hs)} 個（期待 {len(expected)}）")
    nums = [h.num for h in hs]
    use_parsed = all(nums) and sorted(nums) == sorted(expected[:len(nums)]) and len(set(nums)) == len(nums)
    out = {}
    for i, h in enumerate(hs):
        n = h.num if use_parsed else (expected[i] if i < len(expected) else None)
        if n is None:
            continue
        end = (hs[i + 1].lp, hs[i + 1].top) if i + 1 < len(hs) else None
        out[n] = (h, doc.region((h.lp, h.top), end, len(doc)))
    return out


# --------------------------------------------------------------------------
# IPA 冊子
# --------------------------------------------------------------------------

def ipa_answers(src: IpaSource, rep: Report) -> dict:
    import pdfplumber
    try:
        p = http_cache.fetch_pdf(src.ans_url)
    except Exception as e:  # オフライン等
        rep.warn(f"{src.code}: IPA 解答例を取得できない（{e}）。書籍の表の正解を使う")
        return {}
    text = ""
    with pdfplumber.open(p) as pdf:
        for pg in pdf.pages:
            text += (pg.extract_text() or "") + "\n"
    return {int(m.group(1)): m.group(2)
            for m in re.finditer(r"問\s*(\d{1,2})\s*([アイウエオカキクケコ])", nfkc(text))}


def build_ipa(src: IpaSource, src_dir: Path, images_root: Path, rep: Report) -> list[Item]:
    qs = find_file(src_dir, src.patterns)
    if qs is None:
        rep.warn(f"{src.code}: 問題冊子 PDF が見つからない（{src.patterns[0]}）")
        return []
    ipa = ipa_answers(src, rep)
    book_path = find_file(src_dir, src.book_patterns)
    book, expl, bdoc = {}, {}, None
    if book_path:
        bdoc = Doc(book_path)
        book = parse_answer_book_table(bdoc, rep, f"{src.code} 書籍表")
        if book:
            first = min(v["page"] for v in book.values())
            expl = explanation_regions(bdoc, first, sorted(book), rep, f"{src.code} 解説")
    else:
        rep.warn(f"{src.code}: 書籍の解説 PDF が無い（{src.book_patterns[0]}）")

    doc = Doc(qs)
    items = []
    try:
        hs = doc.ipa_headers()
        skip = {n for n in range(1, len(doc) + 1) if doc.is_skip_page(n)}
        for i, h in enumerate(hs):
            n = h.num
            end = (hs[i + 1].lp, hs[i + 1].top) if i + 1 < len(hs) else None
            segs = doc.region((h.lp, h.top), end, len(doc), skip)
            lines = doc.region_lines(segs)
            ch = parse_choices(doc, lines)
            conf = 1.0
            notes = []
            imgs = save_segs(doc, segs, images_root, f"b/{src.code}/q{n:02d}", SCALE_Q)
            if not imgs:
                conf -= 0.5
                notes.append("画像なし")
            b = book.get(n, {})
            a_ipa, a_book = ipa.get(n), b.get("answer")
            a_expl = header_answer(expl[n][0]) if n in expl else None
            answer = a_ipa or a_book or a_expl
            vals = [a for a in (a_ipa, a_book, a_expl) if a]
            ok = bool(vals) and len(set(vals)) == 1
            rep.answer_checks.append((src.code, n, a_ipa, a_book, a_expl, ok))
            if not ok:
                conf -= 0.3
                notes.append(f"正解不一致 IPA={a_ipa} 書籍表={a_book} 解説={a_expl}")
            if not answer:
                rep.warn(f"{src.code} 問{n}: 正解が無い")
                continue
            labels, texts = ch.labels, ch.texts
            if not ch.found:
                conf -= 0.3
                notes.append("解答群が読めない")
            if answer not in labels:
                k = LABELS10.index(answer) + 1
                labels = list(LABELS10[:max(k, len(labels))])
                texts = (texts + [""] * len(labels))[:len(labels)] if texts else [""] * len(labels)
                conf -= 0.2
                notes.append("正解が解答群の範囲外")
            topic = b.get("topic") or ("pseudo" if n <= src.n_pseudo else "security")
            ex_imgs = None
            if n in expl:
                ex_imgs = save_segs(bdoc, expl[n][1], images_root, f"b/expl/{src.code}/q{n:02d}", SCALE_EXPL)
                if expl[n][0].lp != b.get("page"):
                    rep.warn(f"{src.code} 問{n}: 解説の開始ページ {expl[n][0].lp} が表の p.{b.get('page')} と違う")
            elif book_path:
                conf -= 0.1
                notes.append("解説なし")
            it = Item(
                id=f"feb-{src.code}-q{n:02d}", exam_code=src.code, exam_label=src.label, number=n,
                pool=src.pool, b_topic=topic, answer=answer,
                choices=[{"label": l, "text": t} for l, t in zip(labels, texts)],
                images=imgs, body="\n".join(l.text for l in lines),
                source_label=f"出典：{src.src_prefix} 問{n}（IPA）", source_url=src.qs_url,
                tags=b.get("tags") or None, difficulty=b.get("difficulty"),
                explanation_images=ex_imgs or None,
                explanation_source=f"{BOOK_TITLE}付録（{src.book_name}）" if ex_imgs else None,
                parse_confidence=conf, notes=notes, origin="ipa")
            items.append(it)
        got = {it.number for it in items}
        for n in sorted(set(ipa) | set(book)):
            if n not in got:
                rep.warn(f"{src.code}: 問{n} を切り出せなかった")
    finally:
        doc.close()
        if bdoc:
            bdoc.close()
    return items


# --------------------------------------------------------------------------
# 書籍付録（付録2/3/4）
# --------------------------------------------------------------------------

def _sub_structure(doc: Doc, segs: list[Seg]):
    """問の領域から 設問 見出しと 小問 (1)(2)… の位置を探す。"""
    lines = doc.region_lines(segs)
    sl = next((l for l in lines if nfkc(l.text).startswith("設問")), None)
    if sl is None:
        return None, None, {}
    m = re.match(r"設問\s*(\d+)?", nfkc(sl.text))
    setsu = m.group(1) if m else None
    subs = {}
    after = False
    for l in lines:
        if l is sl:
            after = True
            continue
        if not after:
            continue
        col_left = min(x.x0 for x in lines if x.lp == l.lp)
        mm = re.match(r"^\((\d)\)", nfkc(l.text).replace(" ", ""))
        if mm and l.x0 - col_left < 8 and int(mm.group(1)) not in subs:
            subs[int(mm.group(1))] = l
    return sl, setsu, subs


def build_furoku(code: str, fno: int, path: Path, images_root: Path, rep: Report,
                 existing: dict[str, Item]) -> list[Item]:
    doc = Doc(path)
    items: list[Item] = []
    try:
        table = parse_furoku_table(doc, rep, code)
        if not table:
            return []
        nums = sorted(table)
        expl_start = min(v["epage"] for v in table.values())
        ph = doc.book_headers(range(2, expl_start))
        if len(ph) != len(nums):
            rep.warn(f"{code}: 問題見出し {len(ph)} 個（表は {len(nums)} 問）")
        expl = explanation_regions(doc, expl_start, nums, rep, f"{code} 解説")
        ex_source = f"{BOOK_TITLE}付録{fno}"
        for i, h in enumerate(ph):
            if i >= len(nums):
                break
            n = nums[i]
            t = table[n]
            if h.lp != t["qpage"]:
                rep.warn(f"{code} 問{n}: 問題の開始ページ {h.lp} が表の p.{t['qpage']} と違う")
            end = (ph[i + 1].lp, ph[i + 1].top) if i + 1 < len(ph) else None
            segs = doc.region((h.lp, h.top), end, expl_start - 1)
            body = text_of(doc, segs)
            source = nfkc(t["source"])
            eh, esegs = expl.get(n, (None, []))

            # ---- 重複判定（出典が IPA 3 冊のいずれか / 本文がほぼ同一）
            target, why = None, ""
            for rx, scode in RE_SRC_IPA:
                mm = rx.search(source)
                if mm and f"feb-{scode}-q{int(mm.group(1)):02d}" in existing:
                    target, why = existing[f"feb-{scode}-q{int(mm.group(1)):02d}"], "出典が同じ"
                    break
            if target is None:
                # 本文全体（設問・解答群まで）がほぼ同じで正解も同じものだけ同一問題とみなす。
                # IPA は同じ状況設定で設問だけ変えて再出題することがあるため、冒頭だけの一致では統合しない。
                a_here = t["answer"] or (header_answer(eh) if eh else None)
                best = max(((similar(body, it.body, 3000), it) for it in existing.values()
                            if it.group_id is None), key=lambda x: x[0], default=(0, None))
                if best[0] >= 0.9 and a_here and a_here == best[1].answer:
                    target, why = best[1], f"本文が一致（類似度 {best[0]:.2f}）"
                elif best[0] >= 0.6:
                    rep.similar.append((f"{code} 問{n}", best[1].id, round(best[0], 2), a_here, best[1].answer))
            if target is not None:
                a_here = t["answer"] or (header_answer(eh) if eh else None)
                if a_here and a_here != target.answer:
                    rep.warn(f"{code} 問{n}: 統合先 {target.id} と正解が違う（{a_here} / {target.answer}）")
                attached = False
                if not target.explanation_images and esegs:
                    rel = f"b/expl/{target.exam_code}/q{target.number:02d}"
                    imgs = save_segs(doc, esegs, images_root, rel, SCALE_EXPL)
                    if imgs:
                        for it in [target] + [x for x in existing.values()
                                              if target.group_id and x.group_id == target.group_id]:
                            it.explanation_images = imgs
                            it.explanation_source = ex_source
                        attached = True
                rep.merged.append((f"{code} 問{n}〔{source}〕", target.id,
                                   why + ("・解説を紐付け" if attached else "・統合先に解説あり")))
                continue

            pool = "mock" if re.search(r"令和\s*[5-8]\s*年", source) else "study"
            src_label = f"出典：{source}／翔泳社{BOOK_TITLE}付録{fno}"
            ex_imgs = save_segs(doc, esegs, images_root, f"b/expl/{code}/q{n:02d}", SCALE_EXPL) if esegs else []
            base = dict(exam_code=code, exam_label=f"付録{fno}", number=n, pool=pool, b_topic="security",
                        source_label=src_label, source_url=BOOK_URL,
                        explanation_images=ex_imgs or None, explanation_source=ex_source if ex_imgs else None,
                        origin="furoku")
            if eh is not None and eh.lp != t["epage"]:
                rep.warn(f"{code} 問{n}: 解説の開始ページ {eh.lp} が表の p.{t['epage']} と違う")

            sa = parse_sub_answers(eh) if eh else {"kind": "single", "answer": None}
            sl, setsu, subs = _sub_structure(doc, segs)
            multi = (sa["kind"] == "subs" and len(sa["answers"]) >= 2) or \
                    (sa["kind"] == "blanks" and sum(1 for _, a, _ in sa["answers"] if "," not in a) >= 1
                     and len(sa["answers"]) >= 2)
            if not multi:
                # ---- 単独問題
                a_tab, a_exp = t["answer"], (header_answer(eh) if eh else None)
                if sa["kind"] == "subs" and sa["answers"]:
                    a_exp = next(iter(sa["answers"].values()))
                answer = a_tab or a_exp
                conf = 1.0
                notes = []
                vals = [a for a in (a_tab, a_exp) if a]
                if len(set(vals)) > 1:
                    conf -= 0.3
                    notes.append(f"正解不一致 表={a_tab} 解説={a_exp}")
                rep.answer_checks.append((code, n, None, a_tab, a_exp, len(set(vals)) == 1))
                if not answer:
                    rep.warn(f"{code} 問{n}: 正解が無い")
                    continue
                ch = parse_choices(doc, doc.region_lines(segs))
                labels, texts, conf = _fix_labels(ch, answer, conf, notes)
                imgs = save_segs(doc, segs, images_root, f"b/{code}/q{n:02d}", SCALE_Q)
                sub_label = None
                if setsu:
                    sub_label = f"設問{setsu}" + (f"({next(iter(subs))})" if len(subs) == 1 else "")
                items.append(Item(id=f"feb-{code}-q{n:02d}", answer=answer,
                                  choices=[{"label": l, "text": x} for l, x in zip(labels, texts)],
                                  images=imgs, body=body, sub_label=sub_label,
                                  parse_confidence=conf - (0 if imgs else 0.5), notes=notes, **base))
                continue

            # ---- 複数設問（共通の問題文 + 設問ごとの小問）
            group = f"{code}-q{n:02d}"
            reg_start = (segs[0].lp, segs[0].y0)
            stem = slice_segs(segs, reg_start, _cut(doc, sl)) if sl else segs
            stem_imgs = save_segs(doc, stem, images_root, f"b/{code}/q{n:02d}_stem", SCALE_Q)
            stem_text = text_of(doc, stem)
            sub_keys = sorted(subs)
            intro = slice_segs(segs, _cut(doc, sl), _cut(doc, subs[sub_keys[0]])) \
                if (sl and sub_keys) else []
            units = []  # (sub_id, sub_label, answer, sub_no)
            if sa["kind"] == "subs":
                for k, a in sorted(sa["answers"].items()):
                    units.append((f"{setsu or 0}-{k}", f"設問{setsu or ''}({k})", a, k))
            else:
                k = sub_keys[0] if sub_keys else None
                for names, a, unordered in sa["answers"]:
                    if "," in a:
                        rep.skipped.append(f"{code} 問{n} 設問{setsu}({k}) {names}：{a}（複数選択・順不同のため出題しない）")
                        continue
                    nm = names.replace(",", "・")
                    units.append((f"{setsu or 0}-{k or 0}{names.replace(',', '')}",
                                  f"設問{setsu or ''}" + (f"({k})" if k else "") + f" {nm}", a, k))
            for sub_id, sub_label, a, k in units:
                conf = 1.0
                notes = []
                if k in subs:
                    nxt = [x for x in sub_keys if x > k]
                    sub_end = _cut(doc, subs[nxt[0]]) if nxt else None
                    part = slice_segs(segs, _cut(doc, subs[k]), sub_end)
                else:
                    part = slice_segs(segs, _cut(doc, sl), None) if sl else []
                    conf -= 0.3
                    notes.append(f"小問({k}) の位置が見つからない")
                own = intro + part
                own_imgs = save_segs(doc, own, images_root, f"b/{code}/q{n:02d}-{sub_id}", SCALE_Q)
                ch = parse_choices(doc, doc.region_lines(part))
                labels, texts, conf = _fix_labels(ch, a, conf, notes)
                rep.answer_checks.append((code, f"{n} {sub_label}", None, None, a, True))
                items.append(Item(id=f"feb-{code}-q{n:02d}-{sub_id}", answer=a,
                                  choices=[{"label": l, "text": x} for l, x in zip(labels, texts)],
                                  images=stem_imgs + own_imgs, body=stem_text + "\n" + text_of(doc, own),
                                  sub_label=sub_label, group_id=group,
                                  parse_confidence=conf - (0 if own_imgs and stem_imgs else 0.5),
                                  notes=notes, **base))
            if len([u for u in units]) < 2:  # 1 問だけになったらグループにしない
                for it in items:
                    if it.group_id == group:
                        it.group_id = None
    finally:
        doc.close()
    return items


def _cut(doc: Doc, line) -> tuple:
    """行の直前で切る位置 (論理ページ, y)。前の行との中間（空欄の枠が文字より上に出るため top より上で切る）。"""
    prev = [l for l in doc.lp(line.lp).lines if l.bottom <= line.top + 0.5 and l is not line]
    if prev:
        pb = max(l.bottom for l in prev)
        return (line.lp, max(pb + 0.5, min(line.top - 3, (pb + line.top) / 2)))
    return (line.lp, line.top - 5)


def _fix_labels(ch, answer: str, conf: float, notes: list):
    labels, texts = list(ch.labels), list(ch.texts)
    if not ch.found:
        conf -= 0.3
        notes.append("解答群が読めない")
    if answer not in labels:
        k = LABELS10.index(answer) + 1
        labels = list(LABELS10[:max(k, len(labels))])
        texts = [""] * len(labels)
        conf -= 0.2
        notes.append("正解が解答群の範囲外")
    return labels, texts, conf


# --------------------------------------------------------------------------
# DB
# --------------------------------------------------------------------------

def ensure_b_columns(conn: sqlite3.Connection) -> list[str]:
    """SPEC 6.1 の列が無ければ追加する（冪等）。追加した列名を返す。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    added = []
    for name, sql in B_SCHEMA_SQL:
        if name not in have:
            conn.execute(sql)
            added.append(name)
    conn.execute(B_INDEX_SQL)
    conn.commit()
    return added


COLS = ["id", "exam_code", "exam_label", "number", "category_id", "body", "choices", "answer", "image_path",
        "is_calc", "pool", "source_label", "source_url", "explanation_url", "parse_confidence", "part",
        "images", "group_id", "sub_label", "b_topic", "tags", "difficulty", "explanation_images",
        "explanation_source"]
UPSERT_SQL = (f"INSERT INTO questions ({', '.join(COLS)}) VALUES ({', '.join(':' + c for c in COLS)}) "
              "ON CONFLICT(id) DO UPDATE SET "
              + ", ".join(f"{c}=excluded.{c}" for c in COLS if c != "id"))


def write_db(db_path: Path, items: list[Item], rep: Report) -> dict:
    from .__main__ import init_database
    init_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        added = ensure_b_columns(conn)
        with conn:
            conn.executemany(UPSERT_SQL, [it.row() for it in items])
        # 今回生成しなかった古い科目B の行（id 変更・統合など）を消す。回答履歴があれば残して警告。
        ids = {it.id for it in items}
        stale = [r[0] for r in conn.execute("SELECT id FROM questions WHERE part='B'") if r[0] not in ids]
        removed = []
        for qid in stale:
            if conn.execute("SELECT 1 FROM attempts WHERE question_id=? LIMIT 1", (qid,)).fetchone():
                rep.warn(f"古い科目B の行 {qid} は回答履歴があるため残した")
            else:
                conn.execute("DELETE FROM questions WHERE id=?", (qid,))
                removed.append(qid)
        conn.commit()
        tot = dict(conn.execute("SELECT part||'/'||pool, COUNT(*) FROM questions GROUP BY part, pool").fetchall())
    finally:
        conn.close()
    return {"added_columns": added, "removed": removed, "totals": tot}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def import_all(src_dir: Path, images_root: Path, rep: Report) -> list[Item]:
    b_dir = images_root / "b"
    if b_dir.exists():
        shutil.rmtree(b_dir)  # 科目B の画像は毎回作り直す（古い枚数のファイルを残さない）
    items: list[Item] = []
    existing: dict[str, Item] = {}
    for src in IPA_SOURCES:
        t = time.time()
        its = build_ipa(src, src_dir, images_root, rep)
        for it in its:
            existing[it.id] = it
        items += its
        print(f"[OK] {src.code:<8} {src.label:<12} {len(its):>3}問  {time.time() - t:5.1f}s", flush=True)
    for code, fno, pats in FUROKU_SOURCES:
        t = time.time()
        path = find_file(src_dir, pats)
        if path is None:
            rep.warn(f"{code}: PDF が見つからない（{pats[0]}）")
            continue
        its = build_furoku(code, fno, path, images_root, rep, existing)
        for it in its:
            existing[it.id] = it
        items += its
        print(f"[OK] {code:<8} 付録{fno:<10} {len(its):>3}問  {time.time() - t:5.1f}s", flush=True)
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m importer.kamoku_b", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="入力 PDF のディレクトリ（既定 data/kamoku_b_src）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB（既定 data/fe.db）")
    ap.add_argument("--images", default=str(IMAGES_DIR), help="画像の出力先（既定 data/images）")
    ap.add_argument("--offline", action="store_true", help="IPA 解答例 PDF をキャッシュからのみ読む")
    a = ap.parse_args(argv)
    if a.offline:
        http_cache.OFFLINE = True
    src_dir = Path(a.src)
    if not src_dir.is_dir():
        print(f"入力ディレクトリが無い: {src_dir}", file=sys.stderr)
        return 1
    rep = Report()
    t0 = time.time()
    items = import_all(src_dir, Path(a.images), rep)
    if not items:
        print("取り込む問題がありません", file=sys.stderr)
        return 1
    res = write_db(Path(a.db), items, rep)

    print("\n==== 科目B サマリ ====")
    print(f"取り込み {len(items)} 問 / {time.time() - t0:.0f}s")
    if res["added_columns"]:
        print("追加した列:", ", ".join(res["added_columns"]))
    by_src = Counter(it.exam_code for it in items)
    print("ソース別:", dict(by_src))
    print("pool 別:", dict(Counter(it.pool for it in items)))
    print("topic 別:", dict(Counter(it.b_topic for it in items)))
    print("難易度:", dict(Counter(it.difficulty or "なし" for it in items)))
    groups = Counter(it.group_id for it in items if it.group_id)
    print(f"グループ: {len(groups)} 個（設問 {sum(groups.values())} 件）")
    n_expl = sum(1 for it in items if it.explanation_images)
    print(f"解説画像の紐付け: {n_expl}/{len(items)} 問")
    print(f"問題画像: {sum(len(it.images) for it in items)} 枚 / 解説画像: "
          f"{len({p for it in items for p in (it.explanation_images or [])})} 枚")
    bad = [c for c in rep.answer_checks if not c[5]]
    n_ipa = sum(1 for c in rep.answer_checks if c[2])
    print(f"\n正解照合: {len(rep.answer_checks)} 件（うち IPA 解答例あり {n_ipa}）、不一致 {len(bad)} 件")
    for c in bad:
        print(f"   {c[0]} 問{c[1]}: IPA={c[2]} 書籍表={c[3]} 解説見出し={c[4]}")
    if rep.merged:
        print("\n重複統合（付録版は作らない）:")
        for m in rep.merged:
            print(f"   {m[0]} -> {m[1]}（{m[2]}）")
    if rep.similar:
        print("\n類似だが別問題として残したもの（同じ状況設定で設問・正解が違う等）:")
        for s_ in rep.similar:
            print(f"   {s_[0]} ~ {s_[1]} 類似度 {s_[2]} 正解 {s_[3]} / {s_[4]}")
    if rep.skipped:
        print("\n出題しない小問:")
        for s in rep.skipped:
            print("  ", s)
    low = [it for it in items if it.parse_confidence < LOW_CONFIDENCE]
    print(f"\n低confidence (< {LOW_CONFIDENCE}): {len(low)} 件")
    for it in low:
        print(f"   {it.id} {it.parse_confidence:.2f} {'; '.join(it.notes)}")
    if res["removed"]:
        print("\n削除した古い科目B の行:", ", ".join(res["removed"]))
    if rep.warnings:
        print(f"\n警告: {len(rep.warnings)}")
        for w in rep.warnings:
            print("  ", w)
    print("\nDB 全体（part/pool 別）:", res["totals"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
