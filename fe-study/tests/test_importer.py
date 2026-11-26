"""importer の単体テスト・回帰テスト。

    cd fe-study && python -m pytest tests/test_importer.py -q

PDF の回帰テストは data/cache/ にキャッシュ済みの PDF がある場合だけ実行（無ければ skip）。
ネットワークには出ない。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.categories import CATEGORIES, FIELD_NAMES, field_of  # noqa: E402
from importer import http_cache  # noqa: E402
from importer.calc_detect import is_calc, numeric_choice  # noqa: E402
from importer.classify import classify_exam, classify_one, field_by_position, keyword_scores  # noqa: E402
from importer.ipa_fetch import Exam  # noqa: E402

CACHE = ROOT / "data" / "cache" / "pdf"


def _cached(name_part: str):
    hits = sorted(CACHE.glob(f"*{name_part}")) if CACHE.exists() else []
    return hits[0] if hits else None


# ---------------------------------------------------------------- categories

def test_categories_definition():
    assert [c["id"] for c in CATEGORIES] == list(range(1, 24))
    assert {c["field"] for c in CATEGORIES if c["id"] <= 13} == {"te"}
    assert {c["field"] for c in CATEGORIES if 14 <= c["id"] <= 16} == {"ma"}
    assert {c["field"] for c in CATEGORIES if c["id"] >= 17} == {"st"}
    assert set(FIELD_NAMES) == {"te", "ma", "st"}
    by = {c["id"]: c for c in CATEGORIES}
    assert by[11]["name"] == "セキュリティ" and by[11]["major_no"] == 3 and by[11]["major_name"] == "技術要素"
    assert by[16]["major_name"] == "サービスマネジメント" and by[23]["major_name"] == "企業と法務"
    assert field_of(14) == "ma"


def test_keyword_json_covers_all_categories():
    data = json.loads((ROOT / "importer" / "syllabus_keywords.json").read_text(encoding="utf-8"))
    cats = data["categories"]
    assert set(cats) == {str(i) for i in range(1, 24)}
    for cid, c in cats.items():
        assert len(c["keywords"]) >= 20, cid


# ---------------------------------------------------------------- exam meta

def test_exam_meta_pool_and_urls():
    e = Exam("01_aki", "令和元年秋期", "am", "https://www.ipa.go.jp/x_qs.pdf", "https://www.ipa.go.jp/x_ans.pdf",
             "令和元年度 秋期 基本情報技術者試験 午前")
    assert e.pool == "study"
    assert e.source_label(1) == "出典：令和元年度 秋期 基本情報技術者試験 午前 問1（IPA）"
    assert e.explanation_url(3) == "https://www.fe-siken.com/kakomon/01_aki/q3.html"
    for code in ("05_haru", "06_haru", "07_haru", "08_haru", "05_menjo"):
        assert Exam(code, "", "koukai", "", "", "").pool == "mock"
    for code in ("04_menjo", "31_haru", "sample", "23_toku"):
        assert Exam(code, "", "am", "", "", "").pool == "study"


def test_http_cache_refuses_other_hosts():
    with pytest.raises(http_cache.FetchError):
        http_cache.fetch("https://www.fe-siken.com/kakomon/01_aki/q1.html")


# ---------------------------------------------------------------- classify

@pytest.mark.parametrize("text,field,expected", [
    ("16進小数0.Cを10進小数に変換したものはどれか。", "te", 1),
    ("2分探索木として適切なものはどれか。", "te", 2),
    ("コンピュータの高速化技術の一つであるメモリインタリーブに関する記述として，適切なものはどれか。", "te", 3),
    ("あるシステムの今年度のMTBFは3,000時間，MTTRは1,000時間である。稼働率は幾らか。", "te", 4),
    ("仮想記憶方式のコンピュータシステムにおいて，ページイン，ページアウトが多発して応答が遅くなる現象はどれか。", "te", 5),
    ("3次元グラフィックス処理におけるクリッピングの説明はどれか。", "te", 8),
    ("関係データベースの表を第3正規形まで正規化したものはどれか。", "te", 9),
    ("IPv4ネットワークにおいて，ネットワークの疎通確認に使われるものはどれか。ping DHCP", "te", 10),
    ("ドライブバイダウンロード攻撃に該当するものはどれか。マルウェアに感染させる。", "te", 11),
    ("ブラックボックステストに関する記述として，最も適切なものはどれか。", "te", 12),
    ("アジャイル開発手法のスクラムにおいて，デイリースクラムはどれか。", "te", 13),
    ("アローダイアグラムで表されるプロジェクトは，完了までに最少で何日を要するか。", "ma", 14),
    ("サービスマネジメントにおけるインシデント管理の目的はどれか。SLA", "ma", 15),
    ("システム監査人が監査報告書に記載すべき指摘事項はどれか。", "ma", 16),
    ("エンタープライズアーキテクチャを構成するアプリケーションアーキテクチャはどれか。", "st", 17),
    ("コアコンピタンスの説明はどれか。", "st", 19),
    ("IoTの応用事例のうち，HEMSの説明はどれか。", "st", 21),
    ("売上高が100百万円のとき，変動費が60百万円，固定費が30百万円掛かる。損益分岐点売上高は幾らか。", "st", 22),
    ("著作権法によって保護の対象となるものはどれか。", "st", 23),
])
def test_classify_one(text, field, expected):
    assert classify_one(text, field) == expected


def test_keyword_boundaries():
    # "ロック"（DB の排他制御）が "ブロック" に一致しないこと
    ex = []
    keyword_scores("ブロック単位で消去する", ex)
    assert not any(k == "ロック" for _, k, _ in ex)
    # 短い英字略語は単語境界のみ
    ex = []
    keyword_scores("information for everyone", ex)
    assert not any(k == "or" for _, k, _ in ex)


def test_field_by_position_old_am():
    assert field_by_position(1, 80) == "te"
    assert field_by_position(50, 80) == "te"
    assert field_by_position(51, 80) == "ma"
    assert field_by_position(60, 80) == "ma"
    assert field_by_position(61, 80) == "st"


def test_classify_exam_respects_field_and_monotonic_without_text():
    # テキストが無い回（スキャン PDF）: 系統の範囲内で、番号順に単調非減少
    items = [{"number": n, "text": "", "field": field_by_position(n, 80)} for n in range(1, 81)]
    cats = classify_exam(items)
    assert len(cats) == 80
    for it, c in zip(items, cats):
        assert field_of(c) == it["field"]
    assert cats == sorted(cats)
    assert cats[0] == 1 and cats[-1] == 23
    assert len(set(cats)) == 23  # 全中分類が現れる


def test_classify_exam_uses_keywords_over_position():
    items = [{"number": n, "text": "", "field": "te"} for n in range(1, 11)]
    items[1]["text"] = "SQLのSELECT文で，関係データベースの表を結合する。正規化"
    cats = classify_exam(items)
    assert cats[1] == 9


# ---------------------------------------------------------------- calc

@pytest.mark.parametrize("body,choices,expected", [
    ("16進小数0.Cを10進小数に変換したものはどれか。", ["0.12", "0.55", "0.75", "0.84"], True),
    ("1.5Mビット／秒の伝送路を用いて12Mバイトのデータを転送するのに必要な伝送時間は何秒か。ここで，伝送効率を50％とする。",
     ["16", "32", "64", "128"], True),
    ("MTBFが3,000時間，MTTRが1,000時間の装置の稼働率はおよそ幾らか。", ["0.69", "0.73", "0.77", "0.80"], True),
    ("売上高が100百万円のとき，変動費が60百万円，固定費が30百万円掛かる。目標利益18百万円を達成するのに必要な売上高は何百万円か。",
     ["108", "120", "156", "180"], True),
    ("ERPを説明したものはどれか。", ["営業活動にITを活用する", "卸売業を支援する", "企業全体の経営資源を管理する", "電子商取引"], False),
    ("トランスポート層のプロトコルであり，リアルタイム性が重視される場合に用いられるものはどれか。",
     ["HTTP", "IP", "TCP", "UDP"], False),
    ("2分探索木になっている2分木はどれか。", ["16 15 19 10 14", "17 14 19 10 16 18", "18 16 19 15", "20 18 19 10"], False),
    ("", ["", "", "", ""], False),
])
def test_is_calc(body, choices, expected):
    assert is_calc(body, choices) is expected


def test_numeric_choice():
    assert numeric_choice("0.001011")
    assert numeric_choice("6.5")
    assert numeric_choice("1,200 円")
    assert not numeric_choice("UDP")
    assert not numeric_choice("16 15 19 10 14")


# ---------------------------------------------------------------- PDF regression (cache only)

def test_parse_answers_h31_spring():
    from importer.pdf_parse import parse_answers
    ans = _cached("2019h31h_fe_am_ans.pdf")
    if not ans:
        pytest.skip("平成31年春期の解答例 PDF がキャッシュに無い")
    a = parse_answers(ans)
    assert sorted(a) == list(range(1, 81))
    assert a[1] == ("ウ", "te") and a[51] == ("イ", "ma") and a[80] == ("ウ", "st")
    assert all(v[0] in "アイウエ" for v in a.values())


def test_parse_scanned_h31_spring(tmp_path):
    from importer.pdf_parse import parse_exam
    qs, ans = _cached("2019h31h_fe_am_qs.pdf"), _cached("2019h31h_fe_am_ans.pdf")
    if not (qs and ans):
        pytest.skip("平成31年春期の PDF がキャッシュに無い")
    questions, answers, warnings = parse_exam(qs, ans, "31_haru", tmp_path, use_ocr=False)
    assert warnings == []
    assert [q.number for q in questions] == list(range(1, 81))
    for q in questions:
        assert [c["label"] for c in q.choices] == ["ア", "イ", "ウ", "エ"]
        assert q.image_path == f"31_haru/q{q.number:02d}.png"
        assert (tmp_path / q.image_path).exists()
        assert q.parse_confidence >= 0.5
    from PIL import Image
    # 問5（2分探索木の図 4 つ）は 1 ページ内の大きな領域、問1 は小さい
    h1 = Image.open(tmp_path / "31_haru/q01.png").height
    h5 = Image.open(tmp_path / "31_haru/q05.png").height
    assert h5 > 2 * h1


def test_parse_text_pdf_r5(tmp_path):
    from importer.pdf_parse import parse_exam
    qs, ans = _cached("2023r05_fe_kamoku_a_qs.pdf"), _cached("2023r05_fe_kamoku_a_ans.pdf")
    if not (qs and ans):
        pytest.skip("令和5年度 公開問題の PDF がキャッシュに無い")
    questions, answers, warnings = parse_exam(qs, ans, "05_haru", tmp_path, use_ocr=False)
    assert warnings == []
    assert [q.number for q in questions] == list(range(1, 21))
    q1 = questions[0]
    assert q1.body == "16進小数0.Cを10進小数に変換したものはどれか。"
    assert [c["text"] for c in q1.choices] == ["0.12", "0.55", "0.75", "0.84"]
    assert answers[1][0] == "ウ"
    assert q1.image_path is None
    q8 = questions[7]
    assert [c["text"] for c in q8.choices] == ["BOOTP", "DHCP", "MIB", "ping"]
    # 表・図を含む問題は画像化される
    assert questions[1].image_path == "05_haru/q02.png"
    assert questions[9].image_path == "05_haru/q10.png"
    # 全問で選択肢 4 つが空でない
    for q in questions:
        assert all(c["text"] for c in q.choices), q.number
    # 最後の問題に「メモ用紙」が混入しない
    assert "メモ" not in questions[-1].choices[-1]["text"]
