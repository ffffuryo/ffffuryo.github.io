"""計算問題の判定。

計算問題 = 数値を使って答えを求める問題（基数変換、稼働率、伝送時間、損益分岐点など）。
問題文と選択肢のテキストから判定する（スキャン PDF でテキストが無い問題は判定できず False）。
"""
from __future__ import annotations

import re
import unicodedata

# 「何〜か」型の問い（数量を答えさせる）
RE_ASK_QUANTITY = re.compile(
    r"何(ビット|バイト|秒|分|時間|日|か月|ヶ月|カ月|年|回|個|台|件|人|通り|桁|けた|倍|円|万円|千円|百万円|"
    r"ミリ秒|マイクロ秒|ナノ秒|ページ|ブロック|トラック|セクタ|ステップ|%|％|パーセント|画素|ドット|行|本|種類|クロック|サイクル|MIPS)"
)
# 計算を示す語
CALC_WORDS = [
    "求め", "計算", "期待値", "稼働率", "MIPS", "平均", "基数", "何倍", "およそ何", "約何", "最大何", "最小何",
    "最低何", "少なくとも何", "幾ら", "いくら", "の値はどれか", "値として正しい", "値はどれか", "何進数",
    "2進数", "16進数", "8進数", "10進数", "進小数", "ビット列", "補数", "伝送時間", "転送時間", "所要時間",
    "応答時間", "実行時間", "アクセス時間", "ヒット率", "利益", "損益分岐", "原価", "売上高", "減価償却",
    "在庫", "発注量", "回線利用率", "伝送効率", "容量", "所要日数", "最短", "最少所要", "工数", "人月",
    "確率", "割合", "比率", "総額", "合計", "差額", "増加費用", "MTBF", "MTTR", "ROI", "ROE", "NPV",
    "現在価値", "情報量", "ハッシュ値", "剰余", "mod", "f(", "の結果", "演算結果", "実効",
    "率が最も", "が最も高い", "が最も低い", "が最も大きい", "が最も小さい", "短い順", "長い順", "大きい順",
    "小さい順", "多い順", "少ない順", "何日", "何台",
]
NON_CALC_HINTS = ["説明はどれか", "説明したものはどれか", "適切なものはどれか", "該当するものはどれか",
                  "特徴はどれか", "記述として", "組合せはどれか"]

RE_NUM = re.compile(r"(?<![A-Za-z])[-−]?\d+(?:[.,]\d+)*(?:\s*[%％])?")
RE_NUM_CHOICE = re.compile(
    r"^[\s\-−+＋]*[\d.,]+\s*(?:[%％]|ビット|バイト|[KMGTkmgt]?(?:バイト|ビット|bps|B)|秒|ミリ秒|分|時間|日|回|個|台|円|万円|千円|"
    r"百万円|倍|人|件|桁|ページ|通り|画素|MIPS|ナノ秒|マイクロ秒)?[\s/／\d.,]*$"
)
RE_FRACTION_OR_EXPR = re.compile(r"^[\s\d.,+\-−×÷*/／()（）=＝<>≦≧≤≥a-zA-Z\^]+$")


def _n(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def numeric_choice(text: str) -> bool:
    t = _n(text).strip()
    if not t:
        return False
    if len(re.findall(r"\d+(?:\.\d+)?", t)) > 3:
        return False  # "16 15 19 10 14" のような図中の数値の並びは数値の答えではない
    if RE_NUM_CHOICE.match(t):
        return True
    # 0.001011 などの数値、分数・数式だけの選択肢（ただし英字だけの選択肢は除く）
    return bool(RE_FRACTION_OR_EXPR.match(t)) and bool(re.search(r"\d", t))


def calc_score(body: str, choices: list[str]) -> float:
    b = _n(body)
    ch = [_n(c) for c in choices if c is not None]
    score = 0.0
    if RE_ASK_QUANTITY.search(b):
        score += 2.0
    hits = sum(1 for w in CALC_WORDS if _n(w) in b)
    score += min(hits, 3) * 0.7
    nums = RE_NUM.findall(b)
    if len(nums) >= 2:
        score += 0.8
    if len(nums) >= 4:
        score += 0.5
    nonempty = [c for c in ch if c.strip()]
    if nonempty and len(nonempty) >= 3:
        numeric = sum(1 for c in nonempty if numeric_choice(c))
        if numeric == len(nonempty):
            score += 2.0
        elif numeric >= len(nonempty) - 1:
            score += 0.8
    if any(h in b for h in NON_CALC_HINTS) and not RE_ASK_QUANTITY.search(b):
        score -= 1.0
    return score


def is_calc(body: str, choices: list[str] | None = None, threshold: float = 2.0) -> bool:
    """問題文と選択肢テキストから計算問題かどうかを判定する。"""
    if not (body or "").strip():
        return False
    return calc_score(body, choices or []) >= threshold
