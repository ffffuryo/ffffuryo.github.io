"""シラバス Ver.9.2 の中分類（1〜23）を問題ごとに推定する。

方針:
  1. 系統（te/ma/st）は強い事前情報。解答例 PDF の「分野」列（Ｔ/Ｍ/Ｓ）があればそれを使い、
     無ければ問題番号の位置（旧午前 80 問: 1〜50 te, 51〜60 ma, 61〜80 st）から決める。
  2. 系統内では、問題文・選択肢中のシラバスキーワード（syllabus_keywords.json）のスコアで中分類を決める。
  3. 問題はシラバス順（中分類番号の昇順）に並ぶので、「番号順に中分類が単調非減少」を
     柔らかい制約にした動的計画法（Viterbi）で全体を整合させる。キーワードが無い／同点の問題は
     隣接問題と位置（系統内の相対位置と典型的な出題数の分布）から決まる。
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from app.categories import CATEGORIES, FIELD_CATEGORY_IDS, field_of  # noqa: E402

KEYWORDS_PATH = HERE / "syllabus_keywords.json"

# 旧午前（80問）の系統内の典型的な出題数（位置事前分布に使う。おおよその値）
TYPICAL_COUNTS = {
    1: 4, 2: 4, 3: 4, 4: 4, 5: 3, 6: 1.5, 7: 1, 8: 1, 9: 5, 10: 5, 11: 9, 12: 6, 13: 2.5,
    14: 4, 15: 4, 16: 2,
    17: 3, 18: 2.5, 19: 4, 20: 1.5, 21: 3, 22: 3.5, 23: 2.5,
}

# シラバスの用語例に無いが過去問で頻出する語（中分類の判別に効くもの）
EXTRA_KEYWORDS = {
    1: ["進数", "2進", "16進", "補数", "浮動小数点", "論理式", "真理値", "ベン図", "確率", "期待値", "標準偏差",
        "BNF", "逆ポーランド", "ハフマン", "オートマトン", "状態遷移", "誤り検出", "ハミング", "パリティ",
        "機械学習", "ディープラーニング", "ニューラルネットワーク", "AI", "量子化", "標本化", "サンプリング",
        "シフト演算", "桁落ち", "情報落ち", "M/M/1", "待ち行列", "正規分布", "命題"],
    2: ["流れ図", "アルゴリズム", "スタック", "キュー", "リスト", "ポインタ", "配列", "2分木", "2分探索木",
        "ヒープ", "ハッシュ", "探索", "整列", "ソート", "クイックソート", "バブルソート", "再帰", "計算量",
        "オーダ", "疑似言語", "擬似言語", "プログラム言語", "XML", "HTML", "JSON", "Java", "Python",
        "オブジェクト指向プログラム", "コーディング規約", "関数", "変数"],
    3: ["CPU", "プロセッサ", "クロック", "MIPS", "CPI", "パイプライン", "キャッシュメモリ", "キャッシュ",
        "ヒット率", "主記憶", "DRAM", "SRAM", "フラッシュメモリ", "メモリインタリーブ", "割込み", "バス",
        "USB", "Bluetooth", "入出力インタフェース", "GPU", "マルチコア", "RISC", "CISC", "SSD", "磁気ディスク",
        "アクセス時間", "回転待ち", "シーク", "プリンタ", "ディスプレイ", "解像度", "命令実行", "アドレス指定"],
    4: ["稼働率", "MTBF", "MTTR", "フォールトトレラント", "フェールセーフ", "フェールソフト", "フールプルーフ",
        "RAID", "デュアルシステム", "デュプレックスシステム", "ホットスタンバイ", "クラスタ", "スループット",
        "レスポンスタイム", "ターンアラウンドタイム", "クライアントサーバ", "シンクライアント", "仮想化",
        "クラウド", "SaaS", "PaaS", "IaaS", "バックアップ", "ベンチマーク", "信頼性", "直列", "並列",
        "NAS", "SAN", "スケールアウト", "スケールアップ", "VM"],
    5: ["OS", "オペレーティングシステム", "タスク", "プロセス", "スレッド", "スケジューリング", "ラウンドロビン",
        "仮想記憶", "ページング", "ページフォールト", "スラッシング", "ページ置換", "LRU", "FIFO",
        "ファイルシステム", "ディレクトリ", "パス名", "コンパイラ", "インタプリタ", "リンカ", "ローダ",
        "OSS", "オープンソース", "デッドロック", "セマフォ", "排他制御", "スプーリング", "リアルタイムOS",
        "ガーベジコレクション", "メモリリーク", "フラグメンテーション", "ミドルウェア", "API", "シェル"],
    6: ["論理回路", "フリップフロップ", "NAND", "NOR", "半加算器", "全加算器", "組込みシステム", "センサ",
        "アクチュエータ", "電力", "消費電力", "LED", "IoTデバイス", "FPGA", "ASIC", "SoC", "D/A", "A/D",
        "PWM", "マイコン", "ファームウェア", "クロック信号", "タイミングチャート", "論理素子"],
    7: ["ユーザインタフェース", "ユーザーインタフェース", "GUI", "CUI", "ユーザビリティ", "アクセシビリティ",
        "ラジオボタン", "チェックボックス", "プルダウン", "ポップアップ", "画面設計", "帳票設計", "コード設計",
        "チェックディジット", "ヒューマンインタフェース", "UX", "ユニバーサルデザイン", "Webデザイン",
        "ナビゲーション", "ウィザード"],
    8: ["マルチメディア", "画像", "音声", "動画", "JPEG", "MPEG", "PNG", "GIF", "MP3", "PCM", "圧縮",
        "ピクセル", "画素", "色", "RGB", "CMY", "フレームレート", "CG", "レンダリング", "テクスチャ",
        "シェーディング", "アンチエイリアシング", "クリッピング", "ポリゴン", "VR", "AR", "メタバース",
        "サンプリング周波数", "量子化ビット数", "ストリーミング"],
    9: ["データベース", "DBMS", "関係データベース", "SQL", "SELECT", "表", "列", "行", "主キー", "外部キー",
        "正規化", "第1正規形", "第2正規形", "第3正規形", "関数従属", "E-R図", "トランザクション",
        "ACID", "原子性", "コミット", "ロールバック", "ロールフォワード", "ログ", "チェックポイント",
        "排他制御", "ロック", "デッドロック", "インデックス", "ビュー", "結合", "射影", "選択",
        "データウェアハウス", "データマイニング", "NoSQL", "ビッグデータ", "データレイク", "分散データベース",
        "2相コミット", "スキーマ", "ストアドプロシージャ"],
    10: ["ネットワーク", "LAN", "WAN", "OSI", "TCP/IP", "TCP", "UDP", "IP", "IPアドレス", "IPv4", "IPv6",
         "サブネット", "サブネットマスク", "ルータ", "ルーティング", "スイッチ", "ハブ", "ブリッジ",
         "リピータ", "ゲートウェイ", "MACアドレス", "ARP", "DHCP", "DNS", "NAT", "NAPT", "HTTP", "SMTP",
         "POP3", "IMAP", "FTP", "SNMP", "ポート番号", "パケット", "フレーム", "伝送", "ビット/秒", "伝送効率",
         "回線", "無線LAN", "Wi-Fi", "CSMA", "VLAN", "VPN", "プロキシ", "MIME", "ping", "ICMP", "5G", "LPWA",
         "PoE", "メールサーバ", "Webサーバ", "URL", "cookie"],
    11: ["セキュリティ", "暗号", "公開鍵", "秘密鍵", "共通鍵", "ディジタル署名", "デジタル署名", "電子署名",
         "認証", "パスワード", "ハッシュ関数", "マルウェア", "ウイルス", "ワーム", "ランサムウェア",
         "ボット", "フィッシング", "なりすまし", "改ざん", "盗聴", "攻撃", "脆弱性", "SQLインジェクション",
         "クロスサイトスクリプティング", "DoS", "DDoS", "ファイアウォール", "WAF", "IDS", "IPS", "DMZ",
         "PKI", "認証局", "証明書", "TLS", "SSL", "HTTPS", "S/MIME", "ISMS", "リスクアセスメント", "情報漏えい",
         "不正アクセス", "生体認証", "バイオメトリクス", "CSIRT", "SIEM", "ソーシャルエンジニアリング",
         "サンドボックス", "ペネトレーションテスト", "ファジング", "SPF", "DKIM", "ゼロデイ", "標的型",
         "機密性", "完全性", "可用性", "情報セキュリティポリシ", "多要素認証", "2要素認証", "危殆化",
         "メッセージ認証", "CAPTCHA", "ブルートフォース", "パスワードリスト攻撃", "ドライブバイダウンロード"],
    12: ["要件定義", "外部設計", "内部設計", "詳細設計", "システム設計", "ソフトウェア設計", "モジュール",
         "モジュール結合度", "モジュール強度", "凝集度", "結合度", "UML", "クラス図", "シーケンス図",
         "ユースケース", "状態遷移図", "DFD", "データフロー", "テスト", "単体テスト", "結合テスト",
         "システムテスト", "ブラックボックステスト", "ホワイトボックステスト", "同値分割", "限界値分析",
         "スタブ", "ドライバ", "カバレッジ", "網羅", "レビュー", "ウォークスルー", "インスペクション",
         "デザインレビュー", "オブジェクト指向", "カプセル化", "継承", "ポリモーフィズム", "汎化", "集約",
         "デザインパターン", "保守", "移行", "受入れ", "リファクタリング", "バグ", "欠陥", "コードレビュー"],
    13: ["開発プロセス", "ウォーターフォール", "アジャイル", "スクラム", "XP", "エクストリームプログラミング",
         "ペアプログラミング", "テスト駆動開発", "プロトタイピング", "スパイラル", "RAD", "DevOps",
         "CMMI", "SLCP", "共通フレーム", "リバースエンジニアリング", "ソフトウェア再利用", "構成管理",
         "変更管理", "バージョン管理", "継続的インテグレーション", "ライセンス", "特許", "開発環境",
         "ローコード", "マッシュアップ", "スプリント", "プロダクトバックログ", "プロダクトオーナ",
         "デイリースクラム", "レトロスペクティブ", "かんばん", "リーン"],
    14: ["プロジェクト", "プロジェクトマネジメント", "PMBOK", "WBS", "アローダイアグラム", "PERT",
         "クリティカルパス", "ガントチャート", "EVM", "アーンドバリュー", "ファンクションポイント",
         "COCOMO", "工数", "見積", "進捗", "ステークホルダ", "スコープ", "リスク対応", "リスクの移転",
         "リスク回避", "リスク軽減", "リスク受容", "クラッシング", "ファストトラッキング", "マイルストーン",
         "要員", "人月", "責任分担マトリックス", "RACI", "コミュニケーションチャネル", "バーンダウンチャート"],
    15: ["サービスマネジメント", "ITサービス", "ITIL", "SLA", "SLM", "サービスレベル", "インシデント",
         "問題管理", "変更管理", "リリース管理", "構成管理", "サービスデスク", "ヘルプデスク", "可用性管理",
         "キャパシティ管理", "サービス継続", "エスカレーション", "FAQ", "バックアップ", "ファシリティ",
         "UPS", "データセンタ", "運用", "障害", "復旧", "ジャーナル", "ロールフォワード", "SLA", "SLO",
         "サービスカタログ", "ベンチマーキング", "チャットボット"],
    16: ["監査", "システム監査", "監査人", "監査証跡", "監査報告書", "内部統制", "ITガバナンス",
         "独立性", "情報セキュリティ監査", "監査手続", "監査調書", "フォローアップ", "COBIT",
         "職務分掌", "モニタリング", "統制", "リスクコントロールマトリックス"],
    17: ["情報システム戦略", "全体最適化", "エンタープライズアーキテクチャ", "EA", "BPR", "BPM",
         "業務プロセス", "SOA", "BPO", "アウトソーシング", "クラウドサービス", "SaaS", "ソリューション",
         "ERP", "CRM", "SCM", "SFA", "RPA", "DX", "デジタルトランスフォーメーション", "業務改善",
         "ワークフロー", "グループウェア", "テレワーク", "VDI", "シェアードサービス", "情報システム投資",
         "IT投資", "ROI", "システム活用", "データ活用", "BYOD", "システム化計画", "ITポートフォリオ"],
    18: ["システム化構想", "システム化計画", "要件定義", "要求定義", "業務要件", "機能要件", "非機能要件",
         "調達", "RFI", "RFP", "提案依頼書", "情報提供依頼", "見積依頼", "提案書", "ベンダ", "契約",
         "請負", "準委任", "グリーン調達", "グリーン購入", "共通フレーム", "企画プロセス", "利害関係者"],
    19: ["経営戦略", "SWOT", "PPM", "コアコンピタンス", "競争戦略", "差別化", "コストリーダーシップ",
         "ニッチ", "ブルーオーシャン", "M&A", "アライアンス", "マーケティング", "4P", "4C", "STP",
         "セグメンテーション", "ターゲティング", "ポジショニング", "製品ライフサイクル", "プロダクトライフサイクル",
         "バランススコアカード", "KPI", "KGI", "CSF", "ブランド", "価格戦略", "ペネトレーション価格",
         "スキミング", "ロングテール", "顧客満足", "CRM", "One to One", "アンゾフ", "ファイブフォース",
         "バリューチェーン", "ベンチマーキング", "イノベータ理論", "アーリーアダプタ", "オムニチャネル",
         "リテンション", "3C分析", "マーケットバスケット", "コンジョイント", "VRIO"],
    20: ["技術戦略", "技術開発", "MOT", "イノベーション", "破壊的イノベーション", "イノベーションのジレンマ",
         "技術ロードマップ", "デスバレー", "ダーウィンの海", "魔の川", "キャズム", "ハイプサイクル",
         "オープンイノベーション", "リーンスタートアップ", "デザイン思考", "TLO", "産学連携",
         "コンカレントエンジニアリング", "技術ポートフォリオ", "特許戦略", "ビジネスモデルキャンバス"],
    21: ["ビジネスシステム", "POS", "EDI", "電子商取引", "EC", "BtoB", "BtoC", "CtoC", "電子マネー",
         "ICカード", "RFID", "IoT", "組込みシステム", "CAD", "CAM", "FA", "MRP", "生産方式", "かんばん方式",
         "JIT", "セル生産", "ライン生産", "SCM", "トレーサビリティ", "GIS", "ITS", "スマートグリッド",
         "HEMS", "CGM", "SNS", "クラウドファンディング", "フィンテック", "FinTech", "シェアリングエコノミー",
         "ロングテール", "アフィリエイト", "レコメンデーション", "エスクロー", "O2O", "MaaS", "デビットカード",
         "スマートファクトリー", "インダストリー4.0", "ドローン", "ブロックチェーン", "暗号資産"],
    22: ["経営", "組織", "企業", "CSR", "コーポレートガバナンス", "ステークホルダ", "BCP", "事業継続",
         "職能別組織", "事業部制", "マトリックス組織", "プロジェクト組織", "カンパニー制", "CIO", "CEO",
         "CFO", "人事", "OJT", "Off-JT", "e-ラーニング", "HRテック", "ダイバーシティ", "ワークライフバランス",
         "パレート図", "ABC分析", "特性要因図", "散布図", "管理図", "ヒストグラム", "QC七つ道具",
         "親和図", "連関図", "デルファイ法", "ブレーンストーミング", "線形計画法", "在庫", "発注",
         "損益分岐点", "変動費", "固定費", "売上総利益", "営業利益", "経常利益", "当期純利益",
         "貸借対照表", "損益計算書", "キャッシュフロー", "財務諸表", "減価償却", "ROE", "ROA",
         "流動比率", "自己資本比率", "原価", "利益", "売上高", "資産", "負債", "固定資産", "棚卸資産",
         "カーボンフットプリント", "SDGs", "ゲーム理論", "意思決定", "OR", "IE", "デシジョンツリー",
         "回帰分析", "相関"],
    23: ["法", "著作権", "著作物", "特許権", "実用新案権", "意匠権", "商標権", "産業財産権", "知的財産",
         "不正競争防止法", "営業秘密", "個人情報保護法", "個人情報", "プライバシー", "労働基準法",
         "労働者派遣", "派遣", "請負契約", "準委任契約", "下請法", "独占禁止法", "PL法", "製造物責任",
         "不正アクセス禁止法", "サイバーセキュリティ基本法", "電子署名法", "特定電子メール法",
         "プロバイダ責任制限法", "電子帳簿保存法", "コンプライアンス", "ライセンス契約", "ソフトウェアライセンス",
         "シュリンクラップ", "ボリュームライセンス", "クリエイティブコモンズ", "GPL", "標準化", "JIS",
         "ISO", "IEEE", "デファクトスタンダード", "公益通報", "36協定", "フレックスタイム", "裁量労働",
         "刑法", "ウイルス作成罪", "PCIDSS", "情報公開法", "マイナンバー", "GDPR", "オプトアウト",
         "オプトイン", "守秘義務", "NDA"],
}

_NORM_RE = re.compile(r"[\s　]+")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("ー", "ー").replace("－", "-")
    return _NORM_RE.sub("", s).lower()


@lru_cache(maxsize=1)
def load_keywords(path: str | None = None):
    """{category_id: {normalized_keyword: weight}} と df を返す。"""
    p = Path(path) if path else KEYWORDS_PATH
    kw: dict[int, dict[str, float]] = {c["id"]: {} for c in CATEGORIES}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        for cid, c in data.get("categories", {}).items():
            for k, w in c.get("keywords", {}).items():
                nk = norm(k)
                if len(nk) >= 2:
                    kw[int(cid)][nk] = max(kw[int(cid)].get(nk, 0), float(w))
    for cid, words in EXTRA_KEYWORDS.items():
        for k in words:
            nk = norm(k)
            if len(nk) >= 2:
                kw[cid][nk] = max(kw[cid].get(nk, 0), 2.0)
    df: dict[str, int] = {}
    for cid, d in kw.items():
        for k in d:
            df[k] = df.get(k, 0) + 1
    return kw, df


_KATAKANA_ONLY = re.compile(r"^[ァ-ヴー・]+$")
_KANJI2 = re.compile(r"^[\u4e00-\u9fff]{2}$")


def _short_ascii(k: str) -> bool:
    return k.isascii() and len(k) <= 3


def keyword_scores(text: str, explain: list | None = None) -> dict[int, float]:
    """中分類ごとのキーワードスコア（長い一致を優先し、包含される短い一致は数えない）。
    explain にリストを渡すと (cid, keyword, score) を追記する。"""
    t = norm(text)
    if not t:
        return {}
    kw, df = load_keywords()
    matches = []  # (start, end, cid, weight)
    for cid, d in kw.items():
        for k, w in d.items():
            start = 0
            if _KATAKANA_ONLY.match(k):
                # カタカナ語は前後がカタカナでない位置だけ（"ロック" が "ブロック" に当たらない）
                for m in re.finditer(r"(?<![ァ-ヴー])" + re.escape(k) + r"(?![ァ-ヴー])", t):
                    matches.append((m.start(), m.end(), cid, w, k))
                continue
            if _short_ascii(k):
                # 短い英字略語は単語境界で一致させる（"or" が "for" に当たる等を防ぐ）
                for m in re.finditer(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", t):
                    matches.append((m.start(), m.end(), cid, w, k))
                continue
            while True:
                i = t.find(k, start)
                if i < 0:
                    break
                matches.append((i, i + len(k), cid, w, k))
                start = i + 1
    if not matches:
        return {}
    # 長い一致から順に採用し、それに完全に含まれる短い一致を除外（同じ語の複数分類は残す）
    matches.sort(key=lambda m: (-(m[1] - m[0]), m[0]))
    taken: list[tuple[int, int, str]] = []
    scores: dict[int, float] = {}
    seen: set[tuple[int, str]] = set()
    for s, e, cid, w, k in matches:
        if any(ts <= s and e <= te and (te - ts) > (e - s) for ts, te, _ in taken):
            continue
        taken.append((s, e, k))
        if (cid, k) in seen:
            continue  # 同じ語の繰り返しは 1 回だけ数える
        seen.add((cid, k))
        idf = math.log(23.0 / df.get(k, 1)) + 0.3
        length = min(1.0 + (len(k) - 2) * 0.15, 2.2)
        if _KANJI2.match(k):
            length *= 0.5  # 漢字 2 文字の一般的な語は弱める
        sc = w * idf * length
        scores[cid] = scores.get(cid, 0.0) + sc
        if explain is not None:
            explain.append((cid, k, round(sc, 1)))
    return scores


def field_by_position(number: int, total: int) -> str:
    """旧午前 80 問の系統（1〜50 te, 51〜60 ma, 61〜80 st）。他の問題数は比率で近似。"""
    if total == 80:
        return "te" if number <= 50 else ("ma" if number <= 60 else "st")
    r = (number - 0.5) / total
    return "te" if r < 50 / 80 else ("ma" if r < 60 / 80 else "st")


def _interval_prior(rel: float, ids: list[int]) -> dict[int, float]:
    """相対位置 rel (0..1) に対する各中分類の事前スコア（典型的な出題数の累積分布の区間からの距離で減点）。"""
    tot = sum(TYPICAL_COUNTS[i] for i in ids)
    acc = 0.0
    pri = {}
    for i in ids:
        a = acc / tot
        acc += TYPICAL_COUNTS[i]
        b = acc / tot
        d = 0.0 if a <= rel < b else min(abs(rel - a), abs(rel - b))
        pri[i] = -d * 12.0
    return pri


def _position_prior(rel: float, field: str) -> dict[int, float]:
    return _interval_prior(rel, FIELD_CATEGORY_IDS[field])


def classify_exam(items: list[dict], *, text_weight: float = 1.0) -> list[int]:
    """1 回分の問題を一括分類する。

    items: 番号順の [{"number": int, "text": str, "field": 'te'|'ma'|'st'|None}]
    返り値: category_id のリスト（items と同じ順）
    """
    n = len(items)
    if n == 0:
        return []
    known = all(it.get("field") for it in items)
    fields = [it.get("field") or field_by_position(it["number"], n) for it in items]
    # 系統ごとの相対位置
    rel = [0.0] * n
    for f in ("te", "ma", "st"):
        idx = [i for i in range(n) if fields[i] == f]
        for j, i in enumerate(idx):
            rel[i] = (j + 0.5) / len(idx)
    cats = [c["id"] for c in CATEGORIES]
    emis = []
    for i, it in enumerate(items):
        ks = keyword_scores(it.get("text", ""))
        kmax = max(ks.values()) if ks else 1.0
        if known:
            prior = _position_prior(rel[i], fields[i])
        else:
            # 系統の区切りが分からない回（公開問題・サンプル）: 全体の相対位置で 23 分類の事前分布
            prior = _interval_prior((i + 0.5) / n, cats)
        row = {}
        for c in cats:
            kscore = ks.get(c, 0.0)
            # キーワードスコア: 対数で圧縮した絶対量 + その問題内での相対的な強さ
            e = text_weight * (2.0 * math.log1p(kscore) + 6.0 * (kscore / kmax if ks else 0.0))
            pw = (0.35 if ks else 1.0) * (1.0 if known else 0.5)
            e += prior.get(c, -12.0) * pw
            if known and field_of(c) != fields[i]:
                e -= 40.0
            row[c] = e
        emis.append(row)
    # Viterbi: 中分類は番号順に単調非減少を好む（逆行にはペナルティ）
    BACK = 4.0
    score = {c: emis[0][c] for c in cats}
    back = []
    for i in range(1, n):
        new, bp = {}, {}
        # 単調: max over prev <= c
        best_le = -1e18
        arg_le = None
        best_all = max(score.values())
        arg_all = max(score, key=score.get)
        for c in cats:
            if score[c] > best_le:
                best_le, arg_le = score[c], c
            cand1 = (best_le, arg_le)
            cand2 = (best_all - BACK, arg_all)
            v, a = cand1 if cand1[0] >= cand2[0] else cand2
            new[c] = v + emis[i][c]
            bp[c] = a
        back.append(bp)
        score = new
    last = max(score, key=score.get)
    path = [last]
    for bp in reversed(back):
        path.append(bp[path[-1]])
    path.reverse()
    return path


def classify_one(text: str, field: str | None = None) -> int:
    """単独の問題を分類（テスト・補助用）。"""
    ks = keyword_scores(text)
    ids = FIELD_CATEGORY_IDS[field] if field else [c["id"] for c in CATEGORIES]
    if not ks or not any(ks.get(i) for i in ids):
        return ids[0]
    return max(ids, key=lambda i: ks.get(i, 0.0))
