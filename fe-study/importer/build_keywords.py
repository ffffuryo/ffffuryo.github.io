"""シラバス Ver.9.2 PDF から中分類ごとのキーワード辞書を作る。

    python -m importer.build_keywords [--out importer/syllabus_keywords.json]

抽出対象:
  - 小分類名（"1. 離散数学"）         weight 3
  - 項目名（"（1）基数", "① 確率"）     weight 2
  - 用語例の各用語（括弧内の別表記も） weight 2
  - 項目説明文中のカタカナ語・英字略語   weight 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber

from .http_cache import fetch_pdf

SYLLABUS_URL = "https://www.ipa.go.jp/shiken/syllabus/omgdg50000005kpe-att/syllabus_fe_ver9_2.pdf"
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "syllabus_keywords.json"

sys.path.insert(0, str(HERE.parent))
from app.categories import CATEGORIES  # noqa: E402

RE_MIDDLE = re.compile(r"中分類\s*(\d+)\s*[：:]")
RE_SMALL = re.compile(r"^(\d{1,2})\s*[\.．]\s*(\S.*)$")
RE_ITEM = re.compile(r"^（(\d{1,2})）\s*(\S.*)$")
RE_SUB = re.compile(r"^([①-⑳])\s*(\S.*)$")
RE_FOOT = re.compile(r"^-\s*\d+\s*-$|^Copyright|^(テクノロジ系|マネジメント系|ストラテジ系)$")
RE_RUBY = re.compile(r"^[ぁ-ゖ\s]{1,8}$")
RE_KATAKANA = re.compile(r"[ァ-ヴー・]{3,}(?:[ァ-ヴー]+)?")
RE_ABBR = re.compile(r"(?<![A-Za-z])[A-Z][A-Za-z0-9/\-]{1,}(?![A-Za-z])")

STOP = {
    "理解する", "担当する事項に適用する", "目標", "用語例", "など", "その他", "あらまし",
    "システム", "データ", "サービス", "プロセス", "ソフトウェア", "ハードウェア", "コンピュータ",
    "マネジメント", "ネットワーク", "プログラム", "ルール", "モデル", "ツール", "レベル", "タイプ",
    "メリット", "デメリット", "ケース", "パターン", "ポイント", "テスト", "ユーザー", "アプリケーション",
}


def split_top(s: str, seps: str = "，,") -> list[str]:
    """括弧の外側にある区切りだけで分割する。"""
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "（(「":
            depth += 1
        elif ch in "）)」":
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def expand_term(term: str) -> list[str]:
    """'BCD（Binary Coded Decimal：2進化10進）' -> ['BCD', 'Binary Coded Decimal', '2進化10進']"""
    term = term.strip().strip("。")
    m = re.match(r"^(.*?)（(.*)）(.*)$", term)
    res = []
    if m:
        head, inner, tail = m.group(1).strip(), m.group(2), m.group(3).strip()
        if head:
            res.append(head + tail if tail else head)
        for part in split_top(inner, "，,：:"):
            res.extend(expand_term(part))
    else:
        res.append(term)
    return [r for r in res if r]


def title_terms(title: str) -> list[str]:
    title = re.sub(r"\s+", "", title)
    res = expand_term(title)
    for t in list(res):
        parts = re.split(r"と(?=[^\s])|・|及び|，|、", t)
        if len(parts) > 1:
            res.extend(p for p in parts if len(p) >= 2)
    return res


def join_line(a: str, b: str) -> str:
    if a and b and re.match(r"[A-Za-z0-9]", a[-1]) and re.match(r"[A-Za-z0-9]", b[0]):
        return a + " " + b
    return a + b


def clean_kw(k: str) -> str | None:
    k = k.strip().strip("。．.・")
    k = re.sub(r"\s+", " ", k)
    if len(k) < 2 or k in STOP:
        return None
    if re.fullmatch(r"[\d\s\.\-]+", k):
        return None
    return k


def extract_lines(pdf_path: Path) -> list[str]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[7:]:
            for ln in (page.extract_text() or "").split("\n"):
                ln = ln.strip()
                if not ln or RE_FOOT.match(ln) or RE_RUBY.match(ln):
                    continue
                lines.append(ln)
    return lines


def build(pdf_path: Path) -> dict:
    lines = extract_lines(pdf_path)
    kws: dict[int, dict[str, int]] = {c["id"]: {} for c in CATEGORIES}
    smalls: dict[int, list[str]] = {c["id"]: [] for c in CATEGORIES}
    cur = None
    term_buf: str | None = None

    def add(k, w):
        k = clean_kw(k)
        if k and cur:
            kws[cur][k] = max(kws[cur].get(k, 0), w)

    def flush_terms():
        nonlocal term_buf
        if term_buf:
            for t in split_top(term_buf):
                for e in expand_term(t):
                    add(e, 2)
        term_buf = None

    for ln in lines:
        m = RE_MIDDLE.search(ln)
        if m and ln.startswith("大分類"):
            flush_terms()
            cur = int(m.group(1))
            continue
        if cur is None:
            continue
        if ln.startswith("用語例"):
            flush_terms()
            term_buf = ln[len("用語例"):].strip()
            continue
        m_small = RE_SMALL.match(ln)
        m_item = RE_ITEM.match(ln)
        m_sub = RE_SUB.match(ln)
        if m_small or m_item or m_sub or ln.startswith("【") or ln.startswith("➢"):
            flush_terms()
            if m_small and not re.search(r"[。を]", m_small.group(2)):
                title = m_small.group(2)
                smalls[cur].append(re.sub(r"\s+", "", title))
                for t in title_terms(title):
                    add(t, 3)
            elif m_item:
                for t in title_terms(m_item.group(2)):
                    add(t, 2)
            elif m_sub:
                for t in title_terms(m_sub.group(2)):
                    add(t, 2)
            elif ln.startswith("➢"):
                for k in RE_KATAKANA.findall(ln):
                    add(k, 1)
            continue
        if term_buf is not None:
            # 用語例の継続行（説明文が始まったら終わり）
            if ln.endswith("する。") or ln.endswith("を理解する。"):
                flush_terms()
            else:
                term_buf = join_line(term_buf, ln)
                continue
        # 説明文: カタカナ語・英字略語を弱いキーワードとして拾う
        for k in RE_KATAKANA.findall(ln):
            add(k, 1)
        for k in RE_ABBR.findall(ln):
            add(k, 1)
    flush_terms()

    out = {
        "source": SYLLABUS_URL,
        "syllabus": "基本情報技術者試験（レベル2）シラバス Ver.9.2",
        "note": "build_keywords.py により自動生成。weight: 3=小分類名, 2=項目名/用語例, 1=説明文中の語",
        "categories": {},
    }
    for c in CATEGORIES:
        d = kws[c["id"]]
        out["categories"][str(c["id"])] = {
            "name": c["name"],
            "field": c["field"],
            "small": smalls[c["id"]],
            "keywords": dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--pdf", help="ローカルのシラバス PDF（省略時は IPA から取得・キャッシュ）")
    a = ap.parse_args(argv)
    pdf_path = Path(a.pdf) if a.pdf else fetch_pdf(SYLLABUS_URL)
    data = build(pdf_path)
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for cid, c in data["categories"].items():
        print(f"{cid:>2} {c['name']}: 小分類{len(c['small'])} キーワード{len(c['keywords'])}")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
