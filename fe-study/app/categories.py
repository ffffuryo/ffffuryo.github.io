"""基本情報技術者試験（レベル2）シラバス Ver.9.2 の中分類（23 個）。

id は中分類番号（= 過去問道場のカテゴリ番号）。
大分類番号・名称はシラバス Ver.9.2 PDF の目次で確認済み。
"""

FIELD_NAMES = {"te": "テクノロジ系", "ma": "マネジメント系", "st": "ストラテジ系"}

_MAJORS = {
    1: "基礎理論",
    2: "コンピュータシステム",
    3: "技術要素",
    4: "開発技術",
    5: "プロジェクトマネジメント",
    6: "サービスマネジメント",
    7: "システム戦略",
    8: "経営戦略",
    9: "企業と法務",
}

# (中分類番号, 大分類番号, 中分類名)
_MIDDLES = [
    (1, 1, "基礎理論"),
    (2, 1, "アルゴリズムとプログラミング"),
    (3, 2, "コンピュータ構成要素"),
    (4, 2, "システム構成要素"),
    (5, 2, "ソフトウェア"),
    (6, 2, "ハードウェア"),
    (7, 3, "ユーザーインタフェース"),
    (8, 3, "情報メディア"),
    (9, 3, "データベース"),
    (10, 3, "ネットワーク"),
    (11, 3, "セキュリティ"),
    (12, 4, "システム開発技術"),
    (13, 4, "ソフトウェア開発管理技術"),
    (14, 5, "プロジェクトマネジメント"),
    (15, 6, "サービスマネジメント"),
    (16, 6, "システム監査"),
    (17, 7, "システム戦略"),
    (18, 7, "システム企画"),
    (19, 8, "経営戦略マネジメント"),
    (20, 8, "技術戦略マネジメント"),
    (21, 8, "ビジネスインダストリ"),
    (22, 9, "企業活動"),
    (23, 9, "法務"),
]


def field_of(category_id: int) -> str:
    if 1 <= category_id <= 13:
        return "te"
    if 14 <= category_id <= 16:
        return "ma"
    if 17 <= category_id <= 23:
        return "st"
    raise ValueError(f"invalid category id: {category_id}")


CATEGORIES = [
    {
        "id": cid,
        "field": field_of(cid),
        "major_no": major,
        "major_name": _MAJORS[major],
        "name": name,
    }
    for cid, major, name in _MIDDLES
]

CATEGORY_BY_ID = {c["id"]: c for c in CATEGORIES}

FIELD_CATEGORY_IDS = {
    f: [c["id"] for c in CATEGORIES if c["field"] == f] for f in FIELD_NAMES
}
