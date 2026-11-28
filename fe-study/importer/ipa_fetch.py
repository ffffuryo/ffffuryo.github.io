"""IPA の過去問題ページから FE 午前/科目A の問題冊子・解答例 PDF を列挙・取得する。

入口: https://www.ipa.go.jp/shiken/mondai-kaiotu/index.html
  - 年度別アーカイブ (/shiken/mondai-kaiotu/2009h21.html 〜) から旧制度「午前」
  - SG/FE 公開問題 (/shiken/mondai-kaiotu/sg_fe/koukai/2023r05.html 〜) から「科目A」
  - 通年化のお知らせページから 科目A サンプル問題（60問）セット
  - 科目A試験免除制度ページから 令和2〜4年度の修了試験（免除試験）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http_cache import FetchError, fetch, fetch_pdf

BASE = "https://www.ipa.go.jp"
INDEX_URL = BASE + "/shiken/mondai-kaiotu/index.html"
KOUKAI_INDEX_URL = BASE + "/shiken/mondai-kaiotu/sg_fe/koukai/index.html"
SAMPLE_PAGE_URL = BASE + "/shiken/syllabus/henkou/2022/20221226.html"
SAMPLE_PAGE_URL2 = BASE + "/shiken/syllabus/henkou/2022/20220425.html"
MENJO_PAGE_URL = BASE + "/shiken/about/menjo-fe.html"

# 令和2〜4年度の免除試験（修了試験）: 年度ごとに 1 回分を過去問道場の 'NN_menjo' に割り当てる。
# 過去問道場側がどの実施日を収録しているかは未検証（fe-siken.com にはアクセスしない方針のため）。
# 02_menjo は 2020年6月14日実施回（ユーザー確認済み）。過去問道場の 02_menjo もこの回。
# 03 は 6 月回と推定しているが未確認。04 は 6 月回の解答例 PDF が画像のみで正解を自動取得できないため、
# 従来どおり 12 月回のまま据え置き。
MENJO_PICK = {
    "02_menjo": ("20200614", "令和2年度 免除試験",
                 "令和2年度 基本情報技術者試験 科目A試験免除制度 修了試験（2020年6月14日実施）"),
    "03_menjo": ("20210613", "令和3年度 免除試験",
                 "令和3年度 基本情報技術者試験 科目A試験免除制度 修了試験（2021年6月13日実施）"),
    "04_menjo": ("20221211", "令和4年度 免除試験",
                 "令和4年度 基本情報技術者試験 科目A試験免除制度 修了試験（2022年12月11日実施）"),
}


def _verified_codes() -> frozenset:
    codes = {f"{y}_{s}" for y in range(21, 32) for s in ("haru", "aki")}
    codes.discard("31_aki")  # 平成31年に秋期はない（令和元年秋は 01_aki）
    codes.add("01_aki")
    # 02_menjo はユーザー確認済み（2020-06-14 回、問49）。03/04_menjo は未確認のため含めない。
    codes.add("02_menjo")
    return frozenset(codes)


# 過去問道場の問番号と IPA の問番号が一致すると確認できた回だけ解説リンクを付ける。
# 免除試験は 02_menjo のみ確認済み。03/04_menjo・サンプル・23_toku・令和5年以降の公開問題は未確認のためリンクなし。
VERIFIED_EXPLANATION_CODES = _verified_codes()


@dataclass
class Exam:
    code: str            # 過去問道場の回コード
    label: str           # '令和元年秋期'
    kind: str            # 'am' | 'koukai' | 'sample' | 'menjo'
    qs_url: str
    ans_url: str
    source_prefix: str   # '令和元年度 秋期 基本情報技術者試験 午前'
    sort_key: tuple = field(default=(0,))

    def source_label(self, n: int) -> str:
        return f"出典：{self.source_prefix} 問{n}（IPA）"

    @property
    def pool(self) -> str:
        return "mock" if self.code[:3] in ("05_", "06_", "07_", "08_") else "study"

    def explanation_url(self, n: int) -> str:
        if self.code not in VERIFIED_EXPLANATION_CODES:
            return ""
        return f"https://www.fe-siken.com/kakomon/{self.code}/q{n}.html"


RE_AM = re.compile(r"/(\d{4})(h|r)(\d\d)(h|a|tokubetsu)_fe_am_(qs|ans)\.pdf$")
RE_KOUKAI = re.compile(r"/(\d{4})r(\d\d)_fe_kamoku_a_(qs|ans)\.pdf$")


def _links(url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(fetch(url), "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        out.append((a.get_text(" ", strip=True), urljoin(url, a["href"])))
    return out


def discover_am() -> list[Exam]:
    """年度別アーカイブページを辿って旧制度の FE 午前を列挙。"""
    pages = sorted({h for _, h in _links(INDEX_URL)
                    if re.search(r"/shiken/mondai-kaiotu/\d{4}[hr]\d\d\.html$", h)})
    found: dict[tuple, dict] = {}
    for p in pages:
        for _, h in _links(p):
            m = RE_AM.search(h)
            if not m:
                continue
            gy, era, ey, season, kind = m.groups()
            found.setdefault((int(gy), era, ey, season), {})[kind] = h
    exams = []
    for (gy, era, ey, season), urls in sorted(found.items()):
        if "qs" not in urls or "ans" not in urls:
            continue
        ey_i = int(ey)
        if era == "h":
            yy, wa = f"{ey_i:02d}", f"平成{ey_i}年"
        else:
            yy, wa = f"{ey_i:02d}", ("令和元年" if ey_i == 1 else f"令和{ey_i}年")
        if season == "h":
            code, label, src = f"{yy}_haru", f"{wa}春期", f"{wa.replace('年', '年度')} 春期"
            order = 0
        elif season == "a":
            code, label, src = f"{yy}_aki", f"{wa}秋期", f"{wa.replace('年', '年度')} 秋期"
            order = 2
        else:
            code, label, src = f"{yy}_toku", f"{wa}特別", f"{wa.replace('年', '年度')} 特別"
            order = 1
        exams.append(Exam(code, label, "am", urls["qs"], urls["ans"],
                          f"{src} 基本情報技術者試験 午前", (gy, order)))
    return exams


def discover_koukai() -> list[Exam]:
    pages = sorted({h for _, h in _links(KOUKAI_INDEX_URL)
                    if re.search(r"/sg_fe/koukai/\d{4}r\d\d\.html$", h)})
    exams = []
    for p in pages:
        urls = {}
        gy = ry = None
        for _, h in _links(p):
            m = RE_KOUKAI.search(h)
            if m:
                gy, ry, kind = int(m.group(1)), int(m.group(2)), m.group(3)
                urls[kind] = h
        if "qs" in urls and "ans" in urls:
            code = f"{ry:02d}_haru"  # 過去問道場のディレクトリ名（/kakomon/05_haru/ 等）
            exams.append(Exam(code, f"令和{ry}年度", "koukai", urls["qs"], urls["ans"],
                              f"令和{ry}年度 基本情報技術者試験 科目A 公開問題", (gy, 5)))
    return exams


def discover_sample() -> list[Exam]:
    qs = ans = None
    for page in (SAMPLE_PAGE_URL, SAMPLE_PAGE_URL2):
        try:
            for _, h in _links(page):
                if h.endswith("fe_kamoku_a_set_sample_qs.pdf"):
                    qs = h
                elif h.endswith("fe_kamoku_a_set_sample_ans.pdf"):
                    ans = h
        except FetchError:
            continue
        if qs and ans:
            break
    if not (qs and ans):
        return []
    # 過去問道場のコードは未検証。'sample' とする（pool は study）。
    return [Exam("sample", "科目Aサンプル問題", "sample", qs, ans,
                 "基本情報技術者試験 科目A試験 サンプル問題（60問）セット", (2022, 9))]


def discover_menjo() -> list[Exam]:
    links = [h for _, h in _links(MENJO_PAGE_URL)]
    exams = []
    for code, (date, label, src) in MENJO_PICK.items():
        qs = next((h for h in links if h.endswith(f"tokurei_Mondai_{date}_FE.pdf")), None)
        ans = next((h for h in links if h.endswith(f"tokurei_ans_{date}_FE.pdf")), None)
        if qs and ans:
            exams.append(Exam(code, label, "menjo", qs, ans, src, (int(date[:4]), 8)))
    return exams


def discover_all(include_menjo: bool = True, include_sample: bool = True) -> list[Exam]:
    exams = discover_am()
    if include_menjo:
        try:
            exams += discover_menjo()
        except FetchError as e:
            print("WARN menjo:", e)
    if include_sample:
        try:
            exams += discover_sample()
        except FetchError as e:
            print("WARN sample:", e)
    exams += discover_koukai()
    exams.sort(key=lambda e: e.sort_key)
    return exams


def download(exam: Exam):
    """(問題冊子 PDF パス, 解答例 PDF パス) を返す（キャッシュ利用）。"""
    return fetch_pdf(exam.qs_url), fetch_pdf(exam.ans_url)


if __name__ == "__main__":
    for e in discover_all():
        print(e.code, e.label, e.kind, e.pool, e.qs_url)
