"""IPA へのアクセス用 HTTP ヘルパー（キャッシュ・レート制限つき）。

- User-Agent: fe-study-importer/1.0 (personal study)
- 1 リクエストごとに 1 秒以上あける
- 取得物は data/cache/ に保存し、再取得しない
- www.ipa.go.jp 以外へのアクセスは拒否する（fe-siken.com には絶対にアクセスしない）
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "fe-study-importer/1.0 (personal study)"
MIN_INTERVAL = 1.2  # 秒
ALLOWED_HOSTS = {"www.ipa.go.jp"}

ROOT = Path(__file__).resolve().parent.parent  # fe-study/
CACHE_DIR = Path(os.environ.get("FE_CACHE_DIR", ROOT / "data" / "cache"))

_last_request = 0.0
_session: requests.Session | None = None
OFFLINE = False  # True ならネットワークに出ずキャッシュのみ


class FetchError(Exception):
    pass


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def cache_path(url: str) -> Path:
    p = urlparse(url)
    name = p.path.rstrip("/").split("/")[-1] or "index.html"
    sub = "pdf" if name.lower().endswith(".pdf") else "html"
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    return CACHE_DIR / sub / f"{h}_{name}"


def fetch(url: str, *, binary: bool = False) -> bytes | str:
    """URL を取得（キャッシュ優先）。404 等は FetchError。"""
    host = urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise FetchError(f"host not allowed: {host}")
    cp = cache_path(url)
    miss = cp.with_suffix(cp.suffix + ".404")
    if cp.exists():
        data = cp.read_bytes()
        return data if binary else data.decode("utf-8", errors="replace")
    if miss.exists():
        raise FetchError(f"404 (cached): {url}")
    if OFFLINE:
        raise FetchError(f"offline and not cached: {url}")
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        r = _sess().get(url, timeout=60)
    finally:
        _last_request = time.monotonic()
    if r.status_code == 404:
        miss.parent.mkdir(parents=True, exist_ok=True)
        miss.write_text("404")
        raise FetchError(f"404: {url}")
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code}: {url}")
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(cp.suffix + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(cp)
    return r.content if binary else r.content.decode("utf-8", errors="replace")


def fetch_pdf(url: str) -> Path:
    """PDF を取得してキャッシュのパスを返す。"""
    fetch(url, binary=True)
    return cache_path(url)
