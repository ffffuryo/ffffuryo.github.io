"""科目B インポーター用の PDF レイアウト解析ヘルパー。

- 「論理ページ」: 見開き（A4 横に 2 ページ）の PDF は左右に分け、書籍のページ番号どおり
  1, 2, 3, ... と数える（PDF の i ページ目の左 = 2i+1、右 = 2i+2）。縦 1 ページの PDF はそのまま。
- フッタ（「©2023 HASHIMOTO Yuji — 3 — …」「－ 4 －」）は論理ページごとに検出し、切り出しから除外する。
- 見出し: IPA 冊子は行頭の「問N」、書籍は白抜き文字の「問N」バッジ（問題・解説とも）。
- 領域（Seg の並び）を描画して PNG に保存する（pdf_parse._save_png: 16 階調化＋余白トリミング）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from .pdf_parse import _save_png

LABELS10 = "アイウエオカキクケコ"
RE_FOOTER = re.compile(r"©\s*\d{4}|^[－\-—–ー─]\s*[0-9０-９]+\s*[－\-—–ー─]$")
RE_SKIP_PAGE = re.compile(r"メ\s*モ\s*用\s*紙|試験問題に記載されている会社名")
RE_DIGIT = re.compile(r"[0-9０-９]")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def is_white(color) -> bool:
    if color is None:
        return False
    c = tuple(color) if isinstance(color, (list, tuple)) else (color,)
    if len(c) == 4:  # CMYK
        return all(float(v) <= 0.02 for v in c)
    if len(c) == 1:  # Gray
        return float(c[0]) >= 0.98
    if len(c) == 3:  # RGB
        return all(float(v) >= 0.98 for v in c)
    return False


def is_magenta(color) -> bool:
    c = tuple(color) if isinstance(color, (list, tuple)) else ()
    return len(c) == 4 and float(c[1]) >= 0.3 and float(c[0]) <= 0.1 and float(c[2]) <= 0.1


@dataclass
class Line:
    lp: int
    top: float
    bottom: float
    x0: float
    x1: float
    text: str
    chars: list


@dataclass
class LPage:
    no: int            # 論理ページ番号（1 始まり）
    pi: int            # PDF のページ index
    x0: float
    x1: float
    height: float
    lines: list        # フッタを除いた行
    chars: list        # この論理ページの全文字（フッタ含む）
    shapes: list       # rects + curves
    hedges: list       # 水平の罫線
    footer_top: float

    @property
    def width(self):
        return self.x1 - self.x0

    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


@dataclass
class Seg:
    lp: int
    y0: float
    y1: float


@dataclass
class Header:
    lp: int
    top: float          # 切り出し開始 y（バッジ上端）
    char_top: float
    x0: float
    num: int | None     # 読み取れた番号（書籍の重ね文字などで読めない場合 None）
    line_text: str      # 見出し行の文字列（「問2（2）：イ （3）：イ」など）


class Doc:
    """1 つの PDF（論理ページに分割済み）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.plumber = pdfplumber.open(str(path))
        self.pdfium = pdfium.PdfDocument(str(path))
        first = self.plumber.pages[0]
        self.spread = first.width > first.height * 1.2
        self.pages: list[LPage] = []
        for pi, pg in enumerate(self.plumber.pages):
            W = float(pg.width)
            halves = [(0.0, W / 2), (W / 2, W)] if self.spread else [(0.0, W)]
            for k, (x0, x1) in enumerate(halves):
                self.pages.append(self._build(len(self.pages) + 1, pi, pg, x0, x1, self.spread))
        self._renders: dict = {}

    def close(self):
        self._renders.clear()
        try:
            self.plumber.close()
        finally:
            self.pdfium.close()

    def __len__(self):
        return len(self.pages)

    def lp(self, no: int) -> LPage:
        return self.pages[no - 1]

    @staticmethod
    def _build(no, pi, pg, x0, x1, spread=False) -> LPage:
        H = float(pg.height)
        crop = pg.crop((x0, 0, x1, H), strict=False)
        raw = crop.extract_text_lines(layout=False, strip=True, return_chars=True)
        lines = []
        footer_top = None
        for ln in raw:
            t = ln["text"].strip()
            if not t:
                continue
            if ln["top"] > H * 0.85 and RE_FOOTER.search(t.replace(" ", "").replace("　", "")) or (
                    ln["top"] > H * 0.85 and RE_FOOTER.search(t)):
                footer_top = ln["top"] if footer_top is None else min(footer_top, ln["top"])
                continue
            chars = sorted([c for c in ln["chars"] if c["text"].strip()], key=lambda c: c["x0"])
            if spread and re.fullmatch(r"[0-9]{3}", t):
                continue  # 付録4: 書籍本体の（描画されない）ノンブル「492」等
            lines.append(Line(no, ln["top"], ln["bottom"], ln["x0"], ln["x1"], t, chars))
        if footer_top is None:
            footer_top = H - 22
        lines = [l for l in lines if l.top < footer_top - 1]
        lines.sort(key=lambda l: (round(l.top), l.x0))
        shapes = list(crop.rects) + list(crop.curves)
        hedges = [e for e in crop.horizontal_edges if e["width"] > 30]
        return LPage(no, pi, x0, x1, H, lines, list(crop.chars), shapes, hedges, footer_top)

    # ---------------------------------------------------------------- 描画
    def render(self, pi: int, scale: float) -> Image.Image:
        key = (pi, scale)
        if key not in self._renders:
            if len(self._renders) > 6:
                self._renders.clear()
            self._renders[key] = self.pdfium[pi].render(scale=scale, grayscale=True).to_pil().convert("L")
        return self._renders[key]

    def crop_image(self, seg: Seg, scale: float, pad: float = 2.0) -> Image.Image:
        P = self.lp(seg.lp)
        img = self.render(P.pi, scale)
        box = (int(P.x0 * scale), max(0, int((seg.y0 - pad) * scale)),
               min(img.width, int(P.x1 * scale)), min(img.height, int((seg.y1 + pad) * scale)))
        return img.crop(box)

    # ---------------------------------------------------------------- 領域
    def seg_lines(self, seg: Seg) -> list[Line]:
        return [l for l in self.lp(seg.lp).lines if l.top >= seg.y0 - 1.5 and l.bottom <= seg.y1 + 1.5]

    def region_lines(self, segs: list[Seg]) -> list[Line]:
        out = []
        for s in segs:
            out.extend(self.seg_lines(s))
        return out

    def is_skip_page(self, no: int) -> bool:
        """メモ用紙・空白ページ・裏表紙（商標の注記）なら True。"""
        P = self.lp(no)
        t = P.text()
        if not t.strip():
            return True
        return bool(RE_SKIP_PAGE.search(t))

    def barriers(self, no: int) -> list[float]:
        """ページ上部の帯見出し（「サンプル問題・解説」等の色付き帯）の上端 y。領域はそこで打ち切る。"""
        P = self.lp(no)
        out = []
        for s in P.shapes:
            if not s.get("fill") or not is_magenta(s.get("non_stroking_color")):
                continue
            if s["width"] > 0.75 * P.width and 16 <= s["height"] <= 45 and 5 < s["top"] < 80:
                inside = "".join(c["text"] for c in P.chars if s["top"] <= c["top"] <= s["bottom"]
                                 and s["x0"] <= c["x0"] <= s["x1"])
                if "問題" in inside:  # 「サンプル問題・解説」「模擬問題」等の章扉の帯だけ
                    out.append(float(s["top"]))
        # 章の注記の囲み（「付録1は擬似言語のみの解説です…」「この解説は，…の付録です」）
        for l in P.lines:
            if re.match(r"^\s*(付録\s*\d+\s*は|この解説は)", nfkc(l.text)):
                boxes = [s["top"] for s in P.shapes if s["top"] <= l.top and s["bottom"] >= l.bottom
                         and s["x0"] <= l.x0 and s["x1"] >= l.x1 and s["width"] > 0.6 * P.width]
                out.append(float(max(boxes)) if boxes else l.top - 8)
        return out

    def region(self, start: tuple[int, float], end: tuple[int, float] | None, last_lp: int,
               skip_pages: set | None = None) -> list[Seg]:
        """start=(論理ページ, y) から end=(論理ページ, y)（無ければ last_lp の末尾）までの Seg 列。"""
        segs = []
        end_lp = end[0] if end else last_lp
        for no in range(start[0], end_lp + 1):
            if no > len(self.pages):
                break
            if skip_pages and no in skip_pages and no != start[0]:
                continue
            P = self.lp(no)
            y0 = start[1] if no == start[0] else 0.0
            y1 = end[1] if (end and no == end[0]) else P.footer_top - 3
            stop = False
            for b in self.barriers(no):
                if y0 - 1 <= b < y1:
                    y1 = b - 2
                    stop = True
            if y1 - y0 > 6 and (self.seg_lines(Seg(no, y0, y1)) or self._has_shapes(P, y0, y1)):
                segs.append(Seg(no, y0, y1))
            if stop:
                break
        return segs

    @staticmethod
    def _has_shapes(P: LPage, y0, y1) -> bool:
        return any(s["top"] >= y0 and s["bottom"] <= y1 for s in P.shapes)

    # ---------------------------------------------------------------- 見出し
    def ipa_headers(self, first_lp: int = 2) -> list[Header]:
        """IPA 冊子: 行頭（左寄り）の「問N」。"""
        out = []
        for P in self.pages[first_lp - 1:]:
            for l in P.lines:
                m = re.match(r"^問\s*([0-9０-９]{1,2})(?![0-9０-９])", l.text)
                if m and l.x0 < P.x0 + P.width * 0.25:
                    out.append(Header(P.no, l.top - 5, l.top, l.x0, int(nfkc(m.group(1))), l.text))
        return out

    def book_headers(self, lps: range) -> list[Header]:
        """書籍: 白抜き文字の「問N」バッジ（色付きの角丸／帯の上に白文字）。"""
        out: list[Header] = []
        for no in lps:
            if no < 1 or no > len(self.pages):
                continue
            P = self.lp(no)
            for ch in P.chars:
                if ch["text"] != "問" or not is_white(ch.get("non_stroking_color")) or ch["size"] < 7:
                    continue
                if ch["top"] >= P.footer_top - 1:
                    continue
                same = sorted([c for c in P.chars if abs(c["top"] - ch["top"]) < 3 and c is not ch
                               and ch["x0"] < c["x0"] < ch["x1"] + ch["size"] * 3], key=lambda c: c["x0"])
                same = [c for c in same if c["text"] not in "：:\u2009 　"]
                if not same or not RE_DIGIT.match(same[0]["text"]):
                    continue
                if any(h.lp == no and abs(h.char_top - ch["top"]) < 9 and abs(h.x0 - ch["x0"]) < 12
                       for h in out):
                    continue  # 重ね文字（付録4の「問49」と「問1」等）
                digits = ""
                for c in same:
                    if not RE_DIGIT.match(c["text"]):
                        break
                    digits += nfkc(c["text"])
                num = int(digits) if digits and len(digits) <= 2 else None
                top = ch["top"]
                cx, cy = (ch["x0"] + ch["x1"]) / 2, (ch["top"] + ch["bottom"]) / 2
                for s in P.shapes:
                    if s.get("fill") and s["x0"] <= cx <= s["x1"] and s["top"] <= cy <= s["bottom"] \
                            and s["height"] < 30:
                        top = min(top, s["top"])
                line_chars = sorted([c for c in P.chars if abs((c["top"] + c["bottom"]) / 2 - cy) < 5
                                     and c["x0"] >= ch["x0"] - 30], key=lambda c: c["x0"])
                out.append(Header(no, top - 3, ch["top"], ch["x0"], num,
                                  "".join(c["text"] for c in line_chars)))
        out.sort(key=lambda h: (h.lp, h.top))
        return out


# --------------------------------------------------------------------------
# 選択肢
# --------------------------------------------------------------------------

@dataclass
class Choices:
    labels: list
    texts: list
    is_table: bool
    found: bool

    def as_json_list(self):
        return [{"label": l, "text": t} for l, t in zip(self.labels, self.texts)]


def parse_choices(doc: Doc, lines: list[Line], max_text: int = 20) -> Choices:
    """領域の行から「解答群」以降の選択肢ラベル（ア〜コ）を探す。

    短い選択肢（max_text 字以内）は本文を text に入れる。組合せ表・長文は全て空。"""
    idx = [i for i, l in enumerate(lines) if "解答群" in l.text]
    if not idx:
        return Choices([], [], False, False)
    after = lines[idx[-1] + 1:]
    cands = []
    for li, ln in enumerate(after):
        chs = ln.chars
        for ci, c in enumerate(chs):
            if c["text"] not in LABELS10:
                continue
            size = c["size"]
            prv = chs[ci - 1] if ci > 0 else None
            nxt = chs[ci + 1] if ci + 1 < len(chs) else None
            gb = (c["x0"] - prv["x1"]) if prv else 99
            ga = (nxt["x0"] - c["x1"]) if nxt else 99
            if gb >= 0.4 * size and ga >= 0.2 * size:
                cands.append((li, ci, c))
    seq = []
    for cand in cands:
        want = LABELS10[len(seq)] if len(seq) < len(LABELS10) else None
        if cand[2]["text"] == want:
            seq.append(cand)
    if not seq:
        return Choices([], [], False, False)
    labels = [s[2]["text"] for s in seq]
    texts = []
    for k, (li, ci, c) in enumerate(seq):
        nxt = seq[k + 1] if k + 1 < len(seq) else None
        chs = after[li].chars
        end_ci = nxt[1] if (nxt and nxt[0] == li) else len(chs)
        t = _chars_join(chs[ci + 1:end_ci])
        # 次のラベル行までの継続行
        last_li = nxt[0] if nxt else len(after)
        if not (nxt and nxt[0] == li):
            for lj in range(li + 1, last_li):
                if after[lj].x0 > c["x0"] + 2:  # 字下げされた継続行のみ
                    t += _chars_join(after[lj].chars)
                else:
                    break
        texts.append(t.strip())
    # 表（罫線）か
    P = doc.lp(after[seq[0][0]].lp)
    y_top = after[seq[0][0]].top - 22
    y_bot = after[seq[-1][0]].bottom + 4
    is_table = any(y_top <= e["top"] <= y_bot for e in P.hedges)
    if is_table or any(len(t) > max_text for t in texts) or any(not t for t in texts):
        texts = [""] * len(labels)
    return Choices(labels, texts, is_table, True)


def _chars_join(chars: list) -> str:
    s = ""
    prev = None
    for c in chars:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            ascii_pair = prev["text"].isascii() and c["text"].isascii()
            if gap > (0.2 if ascii_pair else 0.6) * c["size"]:
                s += " "
        s += c["text"]
        prev = c
    return s.strip()


# --------------------------------------------------------------------------
# 画像保存
# --------------------------------------------------------------------------

def save_segs(doc: Doc, segs: list[Seg], images_root: Path, rel_prefix: str, scale: float,
              start_k: int = 1) -> list[str]:
    """Seg ごとに PNG を保存し、data/images 相対パスのリストを返す。

    rel_prefix 例 'b/sample/q01' -> b/sample/q01_1.png, q01_2.png ...（インクの無い Seg は捨てる）"""
    out = []
    k = start_k
    for s in segs:
        img = doc.crop_image(s, scale)
        arr = np.asarray(img)
        if arr.size == 0 or (arr < 200).sum() < 30:
            continue
        ink_rows = np.nonzero((arr < 200).any(1))[0]
        if ink_rows[-1] - ink_rows[0] < 6 * scale:  # 罫線・枠の端だけ（6pt 未満）の切れ端は捨てる
            continue
        rel = f"{rel_prefix}_{k}.png"
        _save_png(img, images_root / rel)
        out.append(rel)
        k += 1
    return out
