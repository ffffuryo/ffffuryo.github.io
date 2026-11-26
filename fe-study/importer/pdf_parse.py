"""IPA の問題冊子 PDF / 解答例 PDF の解析。

問題冊子には 2 種類ある:
  1. テキスト層のある PDF（令和5年度以降の公開問題、科目A サンプル問題）
     -> pdfplumber の文字座標で「問N」見出しを探して問題領域を区切り、問題文・選択肢を抽出。
        図・表・数式・プログラム等を含む問題は、問題領域を pypdfium2 でレンダリングして PNG に切り抜く。
  2. スキャン画像のみの PDF（平成21年〜令和元年の午前、免除試験）
     -> テキストが無いため、ページ画像の行（インクの帯）を検出し、左端マージンにある
        「問N」見出し行で問題領域を区切って、問題全体を PNG に切り抜く。
        問題文は空、選択肢は ア〜エ のラベルのみ（text は空）になる。
        tesseract（jpn）がインストールされていれば OCR テキストを分類・計算判定用に得る（任意）。

正解は解答例 PDF（テキスト層あり）から抽出し、問番号で照合する。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

LABELS = ["ア", "イ", "ウ", "エ"]
RENDER_SCALE = 2.0
FIELD_MAP = {"Ｔ": "te", "T": "te", "Ｍ": "ma", "M": "ma", "Ｓ": "st", "S": "st"}


@dataclass
class ParsedQuestion:
    number: int
    body: str
    choices: list            # [{"label": "ア", "text": "..."}]
    image_path: str | None   # data/images/ からの相対パス
    parse_confidence: float
    text_source: str = "pdf"  # 'pdf' | 'image' | 'ocr'
    aux_text: str = ""        # 分類・計算判定用の補助テキスト（OCR 結果など。表示しない）
    notes: list = field(default_factory=list)


# --------------------------------------------------------------------------
# 解答例
# --------------------------------------------------------------------------

RE_ANS = re.compile(r"問\s*([0-9０-９]{1,2})\s*([アイウエ])(?:\s*([ＴＭＳTMS])(?![A-Za-z]))?")


def parse_answers(ans_pdf: Path) -> dict[int, tuple[str, str | None]]:
    """{問番号: (正解, 分野 'te'|'ma'|'st'|None)}"""
    text = ""
    with pdfplumber.open(ans_pdf) as pdf:
        for p in pdf.pages:
            text += (p.extract_text() or "") + "\n"
    res = {}
    for m in RE_ANS.finditer(text):
        n = int(unicodedata.normalize("NFKC", m.group(1)))
        res[n] = (m.group(2), FIELD_MAP.get(m.group(3)) if m.group(3) else None)
    return res


# --------------------------------------------------------------------------
# 共通
# --------------------------------------------------------------------------

def has_text_layer(pdf_path: Path, min_chars: int = 200) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        n = 0
        for p in pdf.pages[: min(8, len(pdf.pages))]:
            n += len(p.chars)
        return n >= min_chars


def _save_png(img: Image.Image, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g = img.convert("L")
    # 16 階調に量子化してファイルサイズを抑える（文字・図の可読性は保つ）
    g = g.point(lambda v: min(255, (v // 16) * 17 + (8 if v > 240 else 0)))
    g = g.point(lambda v: 255 if v >= 232 else v)
    g = _trim_margins(g)
    g.save(out_path, optimize=True)


def _trim_margins(g: Image.Image, pad: int = 8) -> Image.Image:
    """上下左右の白い余白を切り落とす（スマホ幅で文字を少しでも大きく表示するため）。"""
    from PIL import ImageOps
    bbox = ImageOps.invert(g).point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bbox:
        return g
    l, t, r, b = bbox
    return g.crop((max(0, l - pad), max(0, t - pad), min(g.width, r + pad), min(g.height, b + pad)))


def _stack(images: list[Image.Image], gap: int = 12) -> Image.Image:
    if len(images) == 1:
        return images[0]
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("L", (w, h), 255)
    y = 0
    for im in images:
        out.paste(im.convert("L"), (0, y))
        y += im.height + gap
    return out


def _tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(img: Image.Image) -> str:
    """tesseract (jpn) があれば OCR。無ければ空文字。"""
    if not _tesseract_available():
        return ""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "q.png"
        img.save(p)
        try:
            r = subprocess.run(["tesseract", str(p), "stdout", "-l", "jpn", "--psm", "6"],
                               capture_output=True, text=True, timeout=120)
        except Exception:
            return ""
        t = r.stdout if r.returncode == 0 else ""
    # 日本語の文字間に入る空白を除去
    t = re.sub(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])", "", t)
    return t.strip()


# --------------------------------------------------------------------------
# スキャン画像 PDF
# --------------------------------------------------------------------------

@dataclass
class Band:
    y0: int
    y1: int
    x0: int
    x1: int

    @property
    def h(self):
        return self.y1 - self.y0


def _ink(gray: np.ndarray, thr: int = 150) -> np.ndarray:
    ink = gray < thr
    H, W = ink.shape
    # スキャンの縁のノイズを除外
    ink[:, : int(W * 0.03)] = False
    ink[:, int(W * 0.97):] = False
    ink[: int(H * 0.02)] = False
    ink[int(H * 0.985):] = False
    return ink


def _bands(ink: np.ndarray) -> list[Band]:
    rows = ink.sum(1)
    on = rows >= 2
    H = len(on)
    raw = []
    y = 0
    while y < H:
        if on[y]:
            y0 = y
            while y < H and on[y]:
                y += 1
            raw.append([y0, y])
        else:
            y += 1
    merged = []
    for b in raw:
        if merged and b[0] - merged[-1][1] <= 2:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    out = []
    for y0, y1 in merged:
        if y1 - y0 < 4:
            continue
        cols = ink[y0:y1].sum(0)
        xs = np.nonzero(cols > 0)[0]
        if len(xs) == 0:
            continue
        lx = None
        for x in xs:
            if cols[x: x + 6].sum() >= 4:  # 孤立した点（ゴミ）を無視
                lx = int(x)
                break
        if lx is None:
            continue
        rx = int(xs[-1])
        out.append(Band(y0, y1, lx, rx))
    return out


def _is_header_token(ink: np.ndarray, b: Band) -> bool:
    """行頭が「問N」+ 空白 の形か（最初の大きな空白が 0.8〜2.9 文字幅の位置にある）。"""
    h = b.h
    cols = ink[b.y0: b.y1, b.x0: b.x0 + int(h * 4)].sum(0) > 0
    n = len(cols)
    x = 0
    big = 0.6 * h
    while x < n:
        if not cols[x]:
            x2 = x
            while x2 < n and not cols[x2]:
                x2 += 1
            if x2 - x >= big:
                return 0.8 * h <= x <= 2.9 * h and x2 < n  # 空白の後に本文が続く
            x = x2
        else:
            x += 1
    return False


@dataclass
class _Page:
    index: int
    img: Image.Image
    ink: np.ndarray
    bands: list
    content_bottom: int


def _load_scanned_pages(pdf_path: Path, scale: float) -> list[_Page]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    pages = []
    try:
        for i in range(len(pdf)):
            img = pdf[i].render(scale=scale, grayscale=True).to_pil().convert("L")
            gray = np.asarray(img)
            ink = _ink(gray)
            H = gray.shape[0]
            bands = [b for b in _bands(ink) if b.y0 < H * 0.92]  # ページ番号（フッタ）を除外
            bottom = max((b.y1 for b in bands), default=0)
            pages.append(_Page(i, img, ink, bands, bottom))
    finally:
        pdf.close()
    return pages


def _glyph_feature(ink: np.ndarray, b: Band) -> np.ndarray:
    """行頭の 1 文字（「問」のはず）の正規化ビットマップ特徴。"""
    h = b.h
    g = ink[b.y0: b.y1, b.x0: b.x0 + int(1.6 * h)]
    cols = g.sum(0) > 0
    end = len(cols)
    for x in range(int(0.4 * h), len(cols)):
        if not cols[x]:
            end = x
            break
    g = g[:, :end]
    ys = np.nonzero(g.any(1))[0]
    if len(ys) == 0:
        return np.zeros(256)
    g = g[ys.min(): ys.max() + 1]
    im = Image.fromarray(g.astype(np.uint8) * 255).resize((16, 16), Image.LANCZOS)
    v = np.asarray(im, dtype=float).ravel()
    v -= v.mean()
    n = np.linalg.norm(v)
    return v / n if n else v


def _sub_bands(ink: np.ndarray, b: Band, th: float) -> list[Band]:
    """背の高い帯（行内に図記号がある見出し行 + 図 など）を、左端の帯状領域だけで行に分け直す。"""
    strip = ink[b.y0: b.y1, b.x0: b.x0 + int(2.5 * th)]
    on = strip.sum(1) > 0
    out = []
    y = 0
    n = len(on)
    while y < n:
        if on[y]:
            y0 = y
            while y < n and on[y]:
                y += 1
            if 0.6 * th <= y - y0 <= 1.5 * th:
                rows = ink[b.y0 + y0: b.y0 + y, :]
                xs = np.nonzero(rows.any(0))[0]
                out.append(Band(b.y0 + y0, b.y0 + y, int(xs[0]), int(xs[-1])))
        else:
            y += 1
    return out


def find_scanned_headers(pages: list[_Page]) -> list[tuple[int, Band, int]]:
    """全ページから「問N」見出し行を検出して (page_index, band, crop_top) を出現順に返す。"""
    hs = [b.h for p in pages for b in p.bands if b.h < 40]
    th = float(np.median(hs)) if hs else 19.0
    def collect(near):
        out = []
        for p in pages:
            tb = [b for b in p.bands if 0.6 * th <= b.h <= 1.5 * th]
            if not tb:
                continue
            for b in p.bands:
                if not near(p, tb, b):
                    continue
                if 0.6 * th <= b.h <= 1.5 * th:
                    if _is_header_token(p.ink, b):
                        out.append((p.index, b, b.y0))
                elif b.h > 1.5 * th:
                    for sb in _sub_bands(p.ink, b, th):
                        if abs(sb.x0 - b.x0) <= 3 and _is_header_token(p.ink, sb):
                            top = b.y0 if b.y0 >= sb.y0 - 1.5 * th else sb.y0
                            out.append((p.index, sb, top))
                            break
        return out

    # 1 回目: ページ内で最も左にある行を候補に -> 見出しの x 位置（文書全体の中央値）を得る
    cands = collect(lambda p, tb, b: b.x0 <= min(x.x0 for x in tb) + 0.5 * th)
    if not cands:
        return []
    mx = float(np.median([b.x0 for _, b, _ in cands]))
    # 2 回目: 見出しの x 位置にある行（見開きで左右に数 px ずれる程度。余白のゴミに影響されない）
    cands = collect(lambda p, tb, b: abs(b.x0 - mx) <= 1.0 * th)
    # 行頭の文字が「問」であること。多数派の字形（「問」。1桁/2桁で字幅が違うので複数の型がある）
    # に十分似ているものだけ残す。注意事項の「6.」「7.」等の少数派の集まりは除外される。
    if len(cands) >= 10:
        F = np.array([_glyph_feature(pages[i].ink, b) for i, b, _ in cands])
        S = F @ F.T
        np.fill_diagonal(S, -1)
        nb = (S > 0.6).sum(1)
        core = nb >= max(5, 0.08 * len(cands))
        if core.any():
            best_core = np.where(core[None, :], S, -1).max(1)
            cands = [c for c, v in zip(cands, best_core) if v >= 0.6]
    return cands


def _crop(page: _Page, y0: int, y1: int, pad: int = 10) -> Image.Image | None:
    bs = [b for b in page.bands if b.y1 > y0 and b.y0 < y1]
    if not bs:
        return None
    top = max(0, min(b.y0 for b in bs) - pad)
    bot = min(page.img.height, max(b.y1 for b in bs) + pad)
    left = max(0, min(b.x0 for b in bs) - pad)
    right = min(page.img.width, max(b.x1 for b in bs) + pad)
    return page.img.crop((left, top, right, bot))


def parse_scanned(qs_pdf: Path, exam_code: str, numbers: list[int], images_root: Path,
                  scale: float = RENDER_SCALE, use_ocr: bool = True) -> tuple[list[ParsedQuestion], list[str]]:
    """スキャン PDF を問題ごとの PNG に切り分ける。numbers は解答例にある問番号（昇順）。"""
    pages = _load_scanned_pages(qs_pdf, scale)
    headers = find_scanned_headers(pages)
    warnings = []
    expected = len(numbers)
    matched = len(headers) == expected
    if not matched:
        warnings.append(f"{exam_code}: 見出し検出数 {len(headers)} != 解答数 {expected}")
    th = float(np.median([b.h for _, b, _ in headers])) if headers else 19.0
    out = []
    do_ocr = use_ocr and _tesseract_available()
    for k, n in enumerate(numbers):
        if k >= len(headers):
            break
        pi, hb, top = headers[k]
        nxt = headers[k + 1] if k + 1 < len(headers) else None
        parts = []
        page = pages[pi]
        if nxt and nxt[0] == pi:
            end = nxt[2] - 2
        else:
            end = page.content_bottom + 1
        c = _crop(page, top - 1, end)
        if c is not None:
            parts.append(c)
        # 次ページにまたがる問題（次の見出しより前にある本文的な行）。最後の問題は対象外
        # （後ろはメモ用紙・注意事項のページ）。
        if nxt and nxt[0] != pi:
            last_pi = nxt[0]
            for qi in range(pi + 1, last_pi + 1):
                p2 = pages[qi]
                lim = nxt[2] - 2 if (nxt and nxt[0] == qi) else p2.content_bottom + 1
                pre = [b for b in p2.bands if b.y1 <= lim]
                # 本文の字下げ位置（見出しより右、中央寄せのメモ欄等ではない）にある行だけを続きとみなす
                if pre and all(hb.x0 + 0.5 * th <= b.x0 <= hb.x0 + 8 * th and 0.6 * th <= b.h <= 1.5 * th
                               for b in pre[:1]):
                    c2 = _crop(p2, pre[0].y0, lim)
                    if c2 is not None:
                        parts.append(c2)
                else:
                    break
        conf = 0.6 if matched else 0.3
        notes = ["image-only (scanned PDF)"]
        rel = f"{exam_code}/q{n:02d}.png"
        if parts:
            img = _stack(parts)
            _save_png(img, images_root / rel)
            if len(parts) > 1:
                notes.append(f"spans {len(parts)} pages")
        else:
            rel = None
            conf = 0.1
        aux = ""
        src = "image"
        if do_ocr and parts:
            aux = ocr_image(_stack(parts))
            if aux:
                src = "ocr"
        out.append(ParsedQuestion(
            number=n, body="", choices=[{"label": l, "text": ""} for l in LABELS],
            image_path=rel, parse_confidence=conf, text_source=src, aux_text=aux, notes=notes))
    return out, warnings


# --------------------------------------------------------------------------
# テキスト層のある PDF
# --------------------------------------------------------------------------

RE_HEAD = re.compile(r"^問\s*([0-9０-９]{1,2})(?:\s+|(?=[^\d０-９]))")
RE_TRAILER = re.compile(r"メモ用紙|注意事項|試験問題に記載されている|^〔?問題(冊子)?終わり")
RE_FOOTER = re.compile(r"^[－\-–ー]\s*[0-9０-９]+\s*[－\-–ー]$")


@dataclass
class _Line:
    page: int
    top: float
    bottom: float
    x0: float
    x1: float
    text: str
    chars: list


def _line_text(chars: list) -> str:
    s = ""
    prev = None
    for c in chars:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            ascii_pair = prev["text"].isascii() and c["text"].isascii()
            if gap > (0.2 if ascii_pair else 0.45) * c["size"]:
                s += " "
        s += c["text"]
        prev = c
    return s


RE_RUBY_TEXT = re.compile(r"^[ぁ-ゖ\s]{1,8}$")


def _page_lines(page, pi: int) -> list[_Line]:
    raw = page.extract_text_lines(layout=False, strip=True, return_chars=True)
    sizes = [c["size"] for ln in raw for c in ln["chars"] if c["text"].strip()]
    med = float(np.median(sizes)) if sizes else 10.0
    out = []
    for ln in raw:
        t = ln["text"].strip()
        if not t or RE_FOOTER.match(t.replace(" ", "")):
            continue
        chars = [c for c in ln["chars"] if c["text"].strip()]
        # ルビ（ふりがな）だけの行は捨てる
        if chars and RE_RUBY_TEXT.match(t) and max(c["size"] for c in chars) < 0.75 * med:
            continue
        out.append(_Line(pi, ln["top"], ln["bottom"], ln["x0"], ln["x1"], t, chars))
    # 上付き/下付き文字だけの行（2^n の n 等）は最も近い通常行に併合する
    merged: list[_Line] = []
    small = [l for l in out if l.chars and max(c["size"] for c in l.chars) < 0.8 * med]
    normal = [l for l in out if l not in small]
    for l in small:
        cy = (l.top + l.bottom) / 2
        best = min(normal, key=lambda n: abs((n.top + n.bottom) / 2 - cy), default=None)
        if best is not None and abs((best.top + best.bottom) / 2 - cy) <= 1.0 * med:
            best.chars = sorted(best.chars + l.chars, key=lambda c: c["x0"])
            best.text = _line_text(best.chars)
            best.x0 = min(best.x0, l.x0); best.x1 = max(best.x1, l.x1)
            best.top = min(best.top, l.top); best.bottom = max(best.bottom, l.bottom)
        else:
            merged.append(l)
    return sorted(normal + merged, key=lambda l: (l.top, l.x0))


def _join_lines(lines: list[_Line], right_edge: float, keep_breaks: bool = True) -> str:
    buf = ""
    for i, ln in enumerate(lines):
        t = ln.text
        if i == 0:
            buf = t
            continue
        prev = lines[i - 1]
        size = float(np.median([c["size"] for c in prev.chars])) if prev.chars else 10
        wrapped = prev.x1 >= right_edge - 2.5 * size and not prev.text.endswith("。")
        if wrapped or not keep_breaks:
            a, b = buf[-1:], t[:1]
            sep = " " if (re.match(r"[A-Za-z0-9,.)]", a) and re.match(r"[A-Za-z0-9(]", b)) else ""
            buf += sep + t
        else:
            buf += "\n" + t
    return buf


def _chars_text(chars: list) -> str:
    """文字列を座標から組み立てる（行ごと、文字間の大きな空白はスペースに）。"""
    if not chars:
        return ""
    lines: list[list] = []
    for c in sorted(chars, key=lambda c: (c["_line"], c["x0"])):
        if lines and lines[-1][0]["_line"] == c["_line"]:
            lines[-1].append(c)
        else:
            lines.append([c])
    parts = []
    for ln in lines:
        s = ""
        prev = None
        for c in ln:
            if prev is not None:
                gap = c["x0"] - prev["x1"]
                ascii_pair = prev["text"].isascii() and c["text"].isascii()
                if gap > (0.2 if ascii_pair else 0.6) * c["size"]:
                    s += " "
            s += c["text"]
            prev = c
        parts.append(s.strip())
    out = ""
    for p in parts:
        if out and re.match(r"[A-Za-z0-9]", out[-1]) and re.match(r"[A-Za-z0-9]", p[:1]):
            out += " "
        out += p
    return out.strip()


def _find_labels(region_lines: list[_Line]):
    """選択肢ラベル（直後に空白がある ア/イ/ウ/エ）を探す。"""
    cands = []
    for li, ln in enumerate(region_lines):
        chs = ln.chars
        for ci, c in enumerate(chs):
            if c["text"] not in LABELS:
                continue
            size = c["size"]
            nxt = chs[ci + 1] if ci + 1 < len(chs) else None
            prv = chs[ci - 1] if ci > 0 else None
            gap_after = (nxt["x0"] - c["x1"]) if nxt else 99
            gap_before = (c["x0"] - prv["x1"]) if prv else 99
            if gap_after >= 0.45 * size and gap_before >= 0.45 * size:
                cands.append((li, ci, c))
    # 最後に現れる ア→イ→ウ→エ の並びを採用
    best = None
    for s in range(len(cands)):
        if cands[s][2]["text"] != "ア":
            continue
        seq = [cands[s]]
        for t in range(s + 1, len(cands)):
            want = LABELS[len(seq)]
            if cands[t][2]["text"] == want:
                seq.append(cands[t])
                if len(seq) == 4:
                    break
        if len(seq) == 4:
            best = seq
    return best


def _cluster_boxes(boxes: list, pad: float = 4.0) -> list:
    """重なる/近接する図形の bbox をまとめる（図・表の領域）。"""
    cl = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        for b in cl:
            for o in out:
                if b[0] <= o[2] + pad and o[0] <= b[2] + pad and b[1] <= o[3] + pad and o[1] <= b[3] + pad:
                    o[0] = min(o[0], b[0]); o[1] = min(o[1], b[1])
                    o[2] = max(o[2], b[2]); o[3] = max(o[3], b[3])
                    changed = True
                    break
            else:
                out.append(b)
        cl = out
    return cl


def _render_region(pdf_doc, segs, scale: float) -> Image.Image:
    """segs: [(page_index, x0, top, x1, bottom)] (PDF pt)"""
    ims = []
    for pi, x0, top, x1, bottom in segs:
        page = pdf_doc[pi]
        img = page.render(scale=scale, grayscale=True).to_pil().convert("L")
        box = (max(0, int((x0 - 6) * scale)), max(0, int((top - 6) * scale)),
               min(img.width, int((x1 + 6) * scale)), min(img.height, int((bottom + 6) * scale)))
        ims.append(img.crop(box))
    return _stack(ims)


def parse_text_pdf(qs_pdf: Path, exam_code: str, numbers: list[int], images_root: Path,
                   scale: float = RENDER_SCALE) -> tuple[list[ParsedQuestion], list[str]]:
    warnings = []
    with pdfplumber.open(qs_pdf) as pdf:
        all_lines: list[_Line] = []
        page_info = {}
        for pi, page in enumerate(pdf.pages):
            lines = _page_lines(page, pi)
            all_lines.extend(lines)
            graphics = []
            for r in page.rects + page.lines + page.curves + page.images:
                w = r["x1"] - r["x0"]
                h = r["bottom"] - r["top"]
                if w > page.width * 0.95 and h > page.height * 0.9:
                    continue  # ページ枠
                graphics.append((r["x0"], r["top"], r["x1"], r["bottom"]))
            page_info[pi] = (page.width, page.height, graphics)
        if not all_lines:
            return [], [f"{exam_code}: テキストなし"]
        page_clusters = {pi: _cluster_boxes(v[2]) for pi, v in page_info.items()}
        left_margin = min(l.x0 for l in all_lines if RE_HEAD.match(l.text)) if any(
            RE_HEAD.match(l.text) for l in all_lines) else 0
        heads = []
        want = set(numbers)
        for idx, l in enumerate(all_lines):
            m = RE_HEAD.match(l.text)
            if m and l.x0 <= left_margin + 6:
                n = int(unicodedata.normalize("NFKC", m.group(1)))
                if n in want and (not heads or n > heads[-1][1]):
                    heads.append((idx, n))
        found = {n for _, n in heads}
        missing = [n for n in numbers if n not in found]
        if missing:
            warnings.append(f"{exam_code}: 見出しが見つからない問 {missing}")
        right_edge = max(l.x1 for l in all_lines)

        out = []
        pdf_doc = pdfium.PdfDocument(str(qs_pdf))
        try:
            for k, (idx, n) in enumerate(heads):
                end = heads[k + 1][0] if k + 1 < len(heads) else len(all_lines)
                region = all_lines[idx:end]
                # 最後の問題の後ろにあるメモ用紙・注意書き等を除外
                for cut, l in enumerate(region):
                    if cut and RE_TRAILER.search(l.text.replace(" ", "")):
                        region = region[:cut]
                        break
                if k + 1 == len(heads):
                    region = [l for l in region if l.page <= region[0].page + 1]
                for li, ln in enumerate(region):
                    for c in ln.chars:
                        c["_line"] = li
                # 見出し "問N" を本文から取り除く
                head_m = RE_HEAD.match(region[0].text)
                first_chars = region[0].chars
                consumed = len(re.sub(r"\s", "", head_m.group(0)))
                region0_chars = first_chars[consumed:]

                labels = _find_labels(region)
                notes = []
                conf = 1.0
                choices = [{"label": l, "text": ""} for l in LABELS]
                if labels:
                    first_label_line, first_label_ci, _ = labels[0]
                    body_lines = region[:first_label_line]
                    # 選択肢ラベルと同じ行の左側にある文字は本文
                    pre_same_line = region[first_label_line].chars[:first_label_ci]
                    # ラベルごとに文字を割り当てる
                    lab_pos = [(li, c["x0"], lab) for (li, ci, c), lab in zip(labels, LABELS)]
                    assigned = {l: [] for l in LABELS}
                    label_ids = {id(c) for _, _, c in labels}
                    for li in range(first_label_line, len(region)):
                        for c in region[li].chars:
                            if id(c) in label_ids:
                                continue
                            if li == first_label_line and c["x0"] < labels[0][2]["x0"]:
                                continue
                            best = None
                            for (lli, lx, lab) in lab_pos:
                                if lli <= li and lx <= c["x0"] + 1:
                                    if best is None or (lli, lx) > (best[0], best[1]):
                                        best = (lli, lx, lab)
                            if best:
                                assigned[best[2]].append(c)
                    for ch in choices:
                        ch["text"] = _chars_text(assigned[ch["label"]])
                    empty = [c["label"] for c in choices if not c["text"]]
                    if empty:
                        notes.append(f"empty choices {empty}")
                        conf -= 0.15
                    if pre_same_line:
                        notes.append("text left of first label")
                else:
                    body_lines = region
                    notes.append("choice labels not found")
                    conf -= 0.4

                # 本文
                bl = list(body_lines)
                if bl:
                    first = _Line(bl[0].page, bl[0].top, bl[0].bottom, bl[0].x0, bl[0].x1,
                                  _chars_text([dict(c, _line=0) for c in region0_chars]) if region0_chars else "",
                                  region0_chars)
                    bl[0] = first
                # 図・表の枠の中にある文字は本文に入れない（画像で見せる）
                def in_graphics(l: _Line) -> bool:
                    cy = (l.top + l.bottom) / 2
                    for g in page_clusters[l.page]:
                        if g[1] - 3 <= cy <= g[3] + 3 and l.x0 >= g[0] - 6 and l.x1 <= g[2] + 6 \
                                and (g[2] - g[0]) > 15 and (g[3] - g[1]) > 15:
                            return True
                    return False
                fig_lines = [l for l in bl[1:] if in_graphics(l)]
                body = _join_lines([l for l in bl if l.text and l not in fig_lines], right_edge).strip()

                # 図・表・数式の検出 -> 画像化
                segs = {}
                for ln in region:
                    s = segs.setdefault(ln.page, [ln.x0, ln.top, ln.x1, ln.bottom])
                    s[0] = min(s[0], ln.x0); s[1] = min(s[1], ln.top)
                    s[2] = max(s[2], ln.x1); s[3] = max(s[3], ln.bottom)
                need_img = []
                for pg, (x0, top, x1, bottom) in list(segs.items()):
                    # 次の見出しまでの範囲（図はテキスト行の間/下にある）
                    nxt_top = None
                    if k + 1 < len(heads) and all_lines[heads[k + 1][0]].page == pg:
                        nxt_top = all_lines[heads[k + 1][0]].top
                    pw, ph, graphics = page_info[pg]
                    lim = nxt_top - 2 if nxt_top else ph * 0.94
                    gin = [g for g in graphics if g[1] >= top - 4 and g[3] <= lim + 2]
                    if gin:
                        need_img.append("graphics")
                        segs[pg] = [min(x0, min(g[0] for g in gin)), top,
                                    max(x1, max(g[2] for g in gin)), max(bottom, max(g[3] for g in gin))]
                sizes = [c["size"] for ln in region for c in ln.chars]
                if sizes:
                    med = float(np.median(sizes))
                    small = sum(1 for s in sizes if s < 0.8 * med)
                    if small >= 1:
                        need_img.append("sub/superscript")
                # 空白の多い行（表やプログラムをテキストで組んだもの）
                if re.search(r"〔プログラム〕|〔擬似言語|〔表|表\s*$|^表\d|図\s*\d|図$", body, re.M):
                    need_img.append("figure/program reference")
                if not labels or any(not c["text"] for c in choices):
                    need_img.append("choices incomplete")
                image_path = None
                if need_img:
                    rel = f"{exam_code}/q{n:02d}.png"
                    seg_list = [(pg, *v) for pg, v in sorted(segs.items())]
                    img = _render_region(pdf_doc, seg_list, scale)
                    _save_png(img, images_root / rel)
                    image_path = rel
                    notes.append("image: " + ",".join(dict.fromkeys(need_img)))
                    conf -= 0.1
                if not body:
                    conf -= 0.2
                out.append(ParsedQuestion(n, body, choices, image_path, round(max(0.05, conf), 2),
                                          "pdf", "", notes))
        finally:
            pdf_doc.close()
    return out, warnings


def parse_exam(qs_pdf: Path, ans_pdf: Path, exam_code: str, images_root: Path,
               use_ocr: bool = True):
    """(questions, answers, warnings)"""
    answers = parse_answers(ans_pdf)
    numbers = sorted(answers)
    warnings = []
    if not numbers:
        return [], answers, [f"{exam_code}: 解答例から正解を抽出できない"]
    if numbers != list(range(1, len(numbers) + 1)):
        warnings.append(f"{exam_code}: 解答例の問番号が連番でない {numbers[:5]}...")
    if has_text_layer(qs_pdf):
        qs, w = parse_text_pdf(qs_pdf, exam_code, numbers, images_root)
    else:
        qs, w = parse_scanned(qs_pdf, exam_code, numbers, images_root, use_ocr=use_ocr)
    return qs, answers, warnings + w
