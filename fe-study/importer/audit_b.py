"""科目B の問題画像・解説画像（書籍付録の切り抜き）が正しい問に対応しているかを全件監査する。

    cd fe-study
    python -m importer.audit_b                         # data/fe.db と data/images を検査
    python -m importer.audit_b --db PATH --images DIR  # 別の DB・画像ディレクトリ
    python -m importer.audit_b -v                      # 画像ごとの元ページ・見出しも表示
    python -m importer.audit_b --only feb-sample-q18   # 1 問だけ

前提: importer.kamoku_b が画像と一緒に data/images/b/manifest.json（画像 -> 元 PDF・論理ページ・y 範囲・sha1）
を書き出していること。各画像の元領域のテキストを PDF のテキストレイヤーから座標で取り出して、次を検査する。

  解説（explanation_images）
    - 期待する解説: IPA の問は対応する書籍解説 PDF の「問N」（書籍に無ければ、出典がその問である付録の問）、
      付録の問はその付録の「問N」
    - 先頭画像が該当問の見出しバッジから始まる / 他の問の見出しが混入していない
    - 見出しの正解記号（「問N：ア」、付録3 の「（2）：イ」「c：エ」）が DB の answer と一致する
    - 該当問の見出しから次の問の見出しの直前まで（章の帯・メモ用紙を除く）の行がすべて画像に入っている
  問題（images）
    - 先頭画像が「問N」から始まる / 他の問が混入していない / 解答群まで入っている（設問ごとの問は自分の小問）
    - 単独の問は、問の見出しから次の問の見出し直前までの行がすべて画像に入っている

画像の元領域が manifest と合わない（sha1 が違う・manifest に無い）ものも NG にする。
書籍のテキストは標準出力に出すだけで、ファイルには保存しない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from . import kamoku_b as kb  # noqa: E402
from .kamoku_b_pdf import Doc, is_white, nfkc  # noqa: E402

KANA = "アイウエオカキクケコ"
FW_DIGITS = "０１２３４５６７８９"


# --------------------------------------------------------------------------
# PDF 側の見出し
# --------------------------------------------------------------------------

@dataclass
class Head:
    lp: int
    top: float            # 見出し文字の上端
    nums: set             # 読み取れた番号の候補（付録4 は「問49」と「問1」の重ね文字なので 2 つ）
    text: str             # 見出し行の文字列
    kind: str             # 'badge'（書籍の白抜きバッジ）| 'ipa'（IPA 冊子の行頭の問N）
    n: int | None = None  # 解決した問番号（その PDF の表の番号）


class Src:
    """1 つの PDF と、その見出し・表。"""

    def __init__(self, path: Path):
        self.path = path
        self.doc = Doc(path)
        self.is_ipa = not path.name.startswith("FE_B")
        self.table: dict = {}       # 問番号 -> 表の行
        self.orig: dict = {}        # 付録の出典の問番号 -> 付録の問番号（付録4 の重ね文字の解決用）
        self.expl_from = 1          # 解説部の先頭ページ（付録は表の解説ページの最小値）
        rep = kb.Report()
        rep.warn = lambda m: None   # 監査では表の警告を出さない
        if not self.is_ipa and self.doc.lp(1).lines:
            t = kb.parse_furoku_table(self.doc, rep, path.name) if "furoku" in path.name else {}
            if t:
                self.table = t
                self.expl_from = min(v["epage"] for v in t.values())
                for n, v in t.items():
                    m = re.search(r"問\s*(\d+)\s*$", nfkc(v["source"]))
                    if m:
                        self.orig[int(m.group(1))] = n
            else:
                self.table = kb.parse_answer_book_table(self.doc, rep, path.name)
                self.expl_from = min((v["page"] for v in self.table.values()), default=1)
        self.heads = self._find_heads()

    def close(self):
        self.doc.close()

    # ---- 見出し
    def _find_heads(self) -> list[Head]:
        out = []
        for P in self.doc.pages:
            if self.is_ipa:
                for l in P.lines:
                    m = re.match(r"^問\s*([0-9０-９]{1,2})(?![0-9０-９])", l.text)
                    if m and l.x0 < P.x0 + P.width * 0.25:
                        k = int(nfkc(m.group(1)))
                        out.append(Head(P.no, l.top, {str(k)}, l.text, "ipa", k))
                continue
            for ch in P.chars:
                if ch["text"] != "問" or ch["size"] < 7 or not is_white(ch.get("non_stroking_color")):
                    continue
                if ch["top"] >= P.footer_top - 1:
                    continue
                near = sorted([c for c in P.chars if abs(c["top"] - ch["top"]) < 3
                               and ch["x0"] < c["x0"] < ch["x1"] + 30 and is_white(c.get("non_stroking_color"))],
                              key=lambda c: c["x0"])
                digits = ""
                for c in near:
                    t = nfkc(c["text"])
                    if t.isdigit():
                        digits += t
                    elif t != "問":
                        break
                if not digits:
                    continue
                n = self._resolve(digits)
                cy = (ch["top"] + ch["bottom"]) / 2
                line = "".join(c["text"] for c in sorted(
                    [c for c in P.chars if abs((c["top"] + c["bottom"]) / 2 - cy) < 5 and c["x0"] >= ch["x0"] - 30],
                    key=lambda c: c["x0"]))
                dup = [h for h in out if h.lp == P.no and abs(h.top - ch["top"]) < 9]
                if dup:  # 同じバッジの別の層（重ね文字・隠れた文字）
                    h = dup[0]
                    h.nums.add(digits)
                    h.top = min(h.top, ch["top"])
                    if h.n is None:
                        h.n = n
                    if len(line) > len(h.text):
                        h.text = line
                    continue
                out.append(Head(P.no, ch["top"], {digits}, line, "badge", n))
        out.sort(key=lambda h: (h.lp, h.top))
        return out

    def _resolve(self, digits: str) -> int | None:
        """バッジの数字列 -> 表の問番号。付録4 は「問49」と「問1」が重ね描きされ、数字が混ざって取れる
        （「4１9」「531」）ので、表の問番号 n と出典の問番号の数字の並べ替えで照合する。"""
        if not self.table:
            return int(digits) if len(digits) <= 2 else None
        key = sorted(digits)
        rev = {v: k for k, v in self.orig.items()}
        for cand in (lambda n: str(n), lambda n: str(n) + str(rev.get(n, "")), lambda n: str(rev.get(n, ""))):
            hit = [n for n in self.table if sorted(cand(n)) == key]
            if len(hit) == 1:
                return hit[0]
        return None

    def problem_heads(self) -> list[Head]:
        return [h for h in self.heads if h.lp < self.expl_from] if not self.is_ipa else self.heads

    def expl_heads(self) -> list[Head]:
        return [h for h in self.heads if h.lp >= self.expl_from] if not self.is_ipa else []

    def heads_in(self, lp: int, y0: float, y1: float) -> list[Head]:
        return [h for h in self.heads if h.lp == lp and y0 - 3 <= h.top <= y1 - 3]

    def has_ink(self, l) -> bool:
        """行の範囲に実際に描画された濃い画素があるか（白文字・クリップされた隠しテキストを除外する）。"""
        P = self.doc.lp(l.lp)
        S = 1.5
        img = self.doc.render(P.pi, S)
        box = (int(l.x0 * S), int(l.top * S), int(l.x1 * S) + 1, int(l.bottom * S) + 1)
        if box[2] <= box[0] or box[3] <= box[1]:
            return False
        return int(np.asarray(img.crop(box)).min()) < 140

    def page_label(self, lp: int) -> str:
        """書籍は論理ページ＝書籍のページ番号（表の p.N と一致）。IPA 冊子はフッタの「－ N －」。"""
        if self.is_ipa:
            P = self.doc.lp(lp)
            ft = "".join(c["text"] for c in P.chars if c["top"] >= P.footer_top - 1)
            m = re.search(r"(\d+)", nfkc(ft))
            return f"p.{m.group(1)}" if m else f"lp{lp}"
        return f"p.{lp}"

    # ---- 期待範囲（見出し h から次の見出しの直前まで）の行
    def range_lines(self, h: Head, heads: list[Head], last_lp: int) -> list:
        i = heads.index(h)
        nxt = heads[i + 1] if i + 1 < len(heads) else None
        end_lp = nxt.lp if nxt else last_lp
        out = []
        for no in range(h.lp, end_lp + 1):
            if no > len(self.doc):
                break
            if no != h.lp and self.doc.is_skip_page(no):
                continue
            y0 = h.top - 2 if no == h.lp else -1
            y1 = (nxt.top - 1) if (nxt and no == nxt.lp) else 1e9
            stop = False
            for b in self.doc.barriers(no):
                if y0 <= b < y1:
                    y1, stop = b, True
            for l in self.doc.lp(no).lines:
                if l.top >= y0 and l.bottom <= y1 + 1:
                    out.append(l)
            if stop:
                break
        return out


def heading_answer(text: str) -> dict:
    """見出し行（「問1：カ」「：問17カ」「問：20カ」「問1イやさしい」「問2（2）：イ （3）：イ」「問9 c：エ …」）の正解。"""
    t = nfkc(text).replace(" ", "").replace("　", "").replace(" ", "").split("〔")[0]
    subs = re.findall(r"\((\d)\)(?:[a-z]\d?)?:([アイウエオカキクケコ])", t)
    if subs:
        return {"subs": {int(k): a for k, a in subs}}
    blanks = re.findall(r"([a-z](?:,[a-z])*):([アイウエオカキクケコ](?:,[アイウエオカキクケコ])*)", t)
    if blanks:
        return {"blanks": {b: a for b, a in blanks}}
    rest = re.sub(r"[問:：0-9]", "", t)
    m = re.match(r"([アイウエオカキクケコ])", rest)
    return {"single": m.group(1) if m else None}


def expected_answer_from_heading(text: str, sub_label: str | None):
    """sub_label（「設問3(2)」「設問2(1) c」）に対応する見出しの正解。読めなければ None。"""
    ha = heading_answer(text)
    if "single" in ha:
        return ha["single"]
    sl = nfkc(sub_label or "")
    if "subs" in ha:
        m = re.search(r"\((\d)\)", sl)
        if m and int(m.group(1)) in ha["subs"]:
            return ha["subs"][int(m.group(1))]
        return next(iter(ha["subs"].values())) if len(ha["subs"]) == 1 else None
    m = re.search(r"\s([a-z](?:・[a-z])*)$", sl)
    if m:
        return ha["blanks"].get(m.group(1).replace("・", ","))
    return None


# --------------------------------------------------------------------------
# 監査
# --------------------------------------------------------------------------

@dataclass
class Result:
    id: str
    ok: bool = True
    reasons: list = field(default_factory=list)
    info: list = field(default_factory=list)
    expect: str = ""
    epages: str = ""
    qpages: str = ""

    def ng(self, msg):
        self.ok = False
        self.reasons.append(msg)


class Auditor:
    def __init__(self, src_dir: Path, images_root: Path):
        self.src_dir = src_dir
        self.images_root = images_root
        mp = images_root / "b" / "manifest.json"
        self.manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.is_file() else {}
        self.srcs: dict[str, Src] = {}
        # exam_code -> 問題冊子 / 書籍解説 PDF 名
        self.qs_pdf, self.book_pdf = {}, {}
        for s in kb.IPA_SOURCES:
            q, b = kb.find_file(src_dir, s.patterns), kb.find_file(src_dir, s.book_patterns)
            if q:
                self.qs_pdf[s.code] = q.name
            if b:
                self.book_pdf[s.code] = b.name
        for code, _, pats in kb.FUROKU_SOURCES:
            p = kb.find_file(src_dir, pats)
            if p:
                self.qs_pdf[code] = self.book_pdf[code] = p.name

    def src(self, name: str) -> Src:
        if name not in self.srcs:
            self.srcs[name] = Src(self.src_dir / name)
        return self.srcs[name]

    def close(self):
        for s in self.srcs.values():
            s.close()

    # ---- 期待する解説の候補 [(pdf 名, 問番号)]
    def expected_expl(self, row) -> list[tuple[str, int]]:
        code, n = row["exam_code"], row["number"]
        out = []
        if code in {s.code for s in kb.IPA_SOURCES}:
            if code in self.book_pdf and n in self.src(self.book_pdf[code]).table:
                out.append((self.book_pdf[code], n))
            for fcode, _, _ in kb.FUROKU_SOURCES:
                if fcode not in self.book_pdf:
                    continue
                fs = self.src(self.book_pdf[fcode])
                for m, v in fs.table.items():
                    for rx, scode in kb.RE_SRC_IPA:
                        mm = rx.search(nfkc(v["source"]))
                        if mm and scode == code and int(mm.group(1)) == n:
                            out.append((fs.path.name, m))
        elif code in self.book_pdf:
            out.append((self.book_pdf[code], n))
        return out

    def region_of(self, rel: str, res: Result):
        m = self.manifest.get(rel)
        f = self.images_root / rel
        if not f.is_file():
            res.ng(f"画像が無い {rel}")
            return None
        if m is None:
            res.ng(f"manifest に無い {rel}")
            return None
        if hashlib.sha1(f.read_bytes()).hexdigest() != m["sha1"]:
            res.ng(f"画像が manifest と違う（再取り込み前の古い画像？） {rel}")
        return m

    def _lines_in(self, s: Src, m) -> list:
        return [l for l in s.doc.lp(m["lp"]).lines if l.top >= m["y0"] - 1.5 and l.bottom <= m["y1"] + 1.5]

    def _check_cover(self, s: Src, regs: list, want_lines: list, res: Result, what: str):
        """want_lines が全部 regs に入っているか / regs に want_lines 以外の行が入っていないか。"""
        want_ids = {id(l) for l in want_lines}
        got = []
        for m in regs:
            got += self._lines_in(s, m)
        got_ids = {id(l) for l in got}
        miss = [l for l in want_lines if id(l) not in got_ids and s.has_ink(l)]
        extra = [l for l in got if id(l) not in want_ids and s.has_ink(l)]
        if miss:
            pages = sorted({l.lp for l in miss})
            res.ng(f"{what}が欠けている: {len(miss)} 行（{', '.join(s.page_label(p) for p in pages)}）")
        if extra:
            pages = sorted({l.lp for l in extra})
            res.ng(f"{what}に範囲外の行が混入: {len(extra)} 行（{', '.join(s.page_label(p) for p in pages)}）")

    @staticmethod
    def _check_order(regs: list, res: Result, what: str):
        keys = [(r["lp"], r["y0"]) for r in regs]
        if keys != sorted(keys):
            res.ng(f"{what}画像の並びがページ順になっていない")

    def _check_notation(self, s: Src, regs: list, n: int, res: Result):
        """本文中の「問N：ア」「：問N ア」表記（見出しバッジ以外も含む）に他の問の番号が無いか。"""
        bad = set()
        for r in regs:
            for l in self._lines_in(s, r):
                t = nfkc(l.text).replace(" ", "")
                for m in re.finditer(r"(?:^|:)問(\d{1,2})(?::|(?=[アイウエオカキクケコ]))", t):
                    k = int(m.group(1))
                    if k != n and s.orig.get(k) != n:  # 付録4 は出典の番号（問49 等）が重ねて入っている
                        bad.add((k, s.page_label(l.lp)))
        if bad:
            res.ng("他の問の「問N：正解」表記が混入: " + ", ".join(f"問{a}（{b}）" for a, b in sorted(bad)))

    # ---- 解説
    def audit_expl(self, row, res: Result, verbose=False):
        ex = json.loads(row["explanation_images"] or "[]")
        cands = self.expected_expl(row)
        if not cands:
            if ex:
                res.info.append("解説の期待先が無いのに解説画像がある")
            return
        if not ex:
            res.ng("解説画像が無い")
            return
        regs = [self.region_of(p, res) for p in ex]
        if any(r is None for r in regs):
            return
        pdfs = {r["pdf"] for r in regs}
        if len(pdfs) != 1:
            res.ng(f"解説画像が複数の PDF にまたがる {sorted(pdfs)}")
            return
        pdf = pdfs.pop()
        want = [c for c in cands if c[0] == pdf]
        if not want:
            res.ng(f"解説の PDF が違う: {pdf}（期待 {', '.join(f'{a} 問{b}' for a, b in cands)}）")
            return
        n = want[0][1]
        s = self.src(pdf)
        res.expect = f"{pdf.replace('.pdf', '')} 問{n}"
        res.epages = "〜".join(dict.fromkeys(s.page_label(r["lp"]) for r in regs))
        if verbose:
            for p, r in zip(ex, regs):
                hs = s.heads_in(r["lp"], r["y0"], r["y1"])
                txt = " / ".join(l.text[:24] for l in self._lines_in(s, r)[:2])
                res.info.append(f"  {p}: {s.page_label(r['lp'])} y={r['y0']:.0f}-{r['y1']:.0f} 見出し="
                                f"{[(h.n, h.text[:20]) for h in hs]} 先頭「{txt}」")
        self._check_order(regs, res, "解説")
        eh = s.expl_heads()
        target = [h for h in eh if h.n == n]
        if not target:
            res.ng(f"解説 PDF に問{n} の見出しが見つからない")
            return
        h = target[0]
        # 先頭画像が該当問の見出しから
        first = regs[0]
        fh = s.heads_in(first["lp"], first["y0"], first["y1"])
        if not fh or fh[0] is not h:
            got = fh[0].n if fh else None
            res.ng(f"先頭画像が問{n} の見出しから始まらない（先頭の見出し={'問' + str(got) if got else 'なし'}、"
                   f"{s.page_label(first['lp'])}）")
        else:
            above = [l for l in self._lines_in(s, first) if l.bottom < h.top - 1]
            if above:
                res.ng(f"先頭画像の見出しより上に前の問の行が {len(above)} 行混入")
        # 他の問の見出し
        others = sorted({(hh.n, s.page_label(r["lp"])) for r in regs
                         for hh in s.heads_in(r["lp"], r["y0"], r["y1"]) if hh is not h},
                        key=lambda x: (x[0] or 0))
        if others:
            res.ng("他の問の見出しが混入: " + ", ".join(f"問{a}（{b}）" for a, b in others))
        self._check_notation(s, regs, n, res)
        # 正解記号
        a = expected_answer_from_heading(h.text, row["sub_label"])
        if a is None:
            res.ng(f"解説見出しの正解記号が読めない「{h.text[:30]}」")
        elif a != row["answer"]:
            res.ng(f"解説見出しの正解 {a} ≠ DB の answer {row['answer']}")
        # 途中で切れていないか（次の問の見出し直前まで）
        self._check_cover(s, regs, s.range_lines(h, eh, len(s.doc)), res, "解説")

    # ---- 問題
    def audit_problem(self, row, res: Result, verbose=False):
        imgs = json.loads(row["images"] or "[]")
        if not imgs:
            res.ng("問題画像が無い")
            return
        regs = [self.region_of(p, res) for p in imgs]
        if any(r is None for r in regs):
            return
        pdf = self.qs_pdf.get(row["exam_code"])
        if {r["pdf"] for r in regs} != {pdf}:
            res.ng(f"問題画像の PDF が違う {sorted({r['pdf'] for r in regs})}（期待 {pdf}）")
            return
        s = self.src(pdf)
        n = row["number"]
        self._check_order(regs, res, "問題")
        res.qpages = "〜".join(dict.fromkeys(s.page_label(r["lp"]) for r in regs))
        ph = s.problem_heads()
        target = [h for h in ph if h.n == n]
        if not target:
            res.ng(f"問題 PDF に問{n} の見出しが見つからない")
            return
        h = target[0]
        first = regs[0]
        fh = s.heads_in(first["lp"], first["y0"], first["y1"])
        if not fh or fh[0] is not h:
            got = fh[0].n if fh else None
            res.ng(f"問題の先頭画像が問{n} から始まらない（先頭の見出し={'問' + str(got) if got else 'なし'}）")
        others = sorted({hh.n or 0 for r in regs for hh in s.heads_in(r["lp"], r["y0"], r["y1"]) if hh is not h})
        if others:
            res.ng("問題画像に他の問が混入: " + ", ".join(f"問{x}" for x in others))
        stems = [r for p, r in zip(imgs, regs) if "_stem_" in p]
        own = [r for p, r in zip(imgs, regs) if "_stem_" not in p]
        own_lines = [l for r in own for l in self._lines_in(s, r)]
        if not any("解答群" in l.text for l in own_lines):
            res.ng("問題画像に解答群が無い")
        if row["group_id"]:
            m = re.search(r"\((\d)\)", nfkc(row["sub_label"] or ""))
            subs = {int(x.group(1)) for x in (re.match(r"^\((\d)\)", nfkc(l.text).replace(" ", ""))
                                              for l in own_lines if l.x0 - min(o.x0 for o in own_lines
                                                                               if o.lp == l.lp) < 8) if x}
            if m and int(m.group(1)) not in subs:
                res.ng(f"設問の画像に小問({m.group(1)}) が無い")
            if m and subs - {int(m.group(1))}:
                res.ng("設問の画像に他の小問が混入: " + ", ".join(f"({x})" for x in sorted(subs - {int(m.group(1))})))
            if stems and not s.heads_in(stems[0]["lp"], stems[0]["y0"], stems[0]["y1"]):
                res.ng("共通問題文の先頭に問の見出しが無い")
        else:
            last = s.expl_from - 1 if not s.is_ipa else len(s.doc)
            self._check_cover(s, regs, s.range_lines(h, ph, last), res, "問題")

    def run(self, rows, verbose=False) -> list[Result]:
        out = []
        for r in rows:
            res = Result(r["id"])
            if not self.manifest:
                res.ng("manifest.json が無い（python -m importer.kamoku_b で作り直す）")
            else:
                self.audit_expl(r, res, verbose)
                self.audit_problem(r, res, verbose)
            out.append(res)
        # 同じ解説画像を別の問（同じグループの設問どうし以外）が共有していないか
        owner: dict = {}
        for r in rows:
            for p in json.loads(r["explanation_images"] or "[]"):
                owner.setdefault(p, set()).add(r["group_id"] or r["id"])
        byid = {res.id: res for res in out}
        for r in rows:
            shared = {o for p in json.loads(r["explanation_images"] or "[]") for o in owner[p]} - {r["group_id"] or r["id"]}
            if shared:
                byid[r["id"]].ng("解説画像を別の問と共有: " + ", ".join(sorted(shared)))
        return out


def load_rows(db: Path, only: str | None = None):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM questions WHERE part='B' ORDER BY id")]
    finally:
        conn.close()
    if only:
        rows = [r for r in rows if r["id"] == only or r["id"].startswith(only)]
    return rows


def audit(db: Path, images: Path, src: Path, verbose=False, only=None) -> list[Result]:
    a = Auditor(src, images)
    try:
        return a.run(load_rows(db, only), verbose)
    finally:
        a.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m importer.audit_b", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(kb.DEFAULT_DB))
    ap.add_argument("--images", default=str(kb.IMAGES_DIR))
    ap.add_argument("--src", default=str(kb.DEFAULT_SRC))
    ap.add_argument("--only", help="問題 ID（前方一致）")
    ap.add_argument("-v", "--verbose", action="store_true", help="画像ごとの元ページ・見出しを表示")
    a = ap.parse_args(argv)
    results = audit(Path(a.db), Path(a.images), Path(a.src), a.verbose, a.only)
    w = max((len(r.id) for r in results), default=10)
    print(f"{'問題ID':<{w - 2}}  判定  解説（期待）                 解説ページ   問題ページ  理由")
    for r in results:
        print(f"{r.id:<{w}}  {'OK' if r.ok else 'NG'}    {r.expect:<26} {r.epages:<12} {r.qpages:<10} "
              f"{' / '.join(r.reasons)}")
        for i in r.info:
            print("      " + i)
    ng = [r for r in results if not r.ok]
    print(f"\n監査 {len(results)} 問: OK {len(results) - len(ng)} / NG {len(ng)}")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
