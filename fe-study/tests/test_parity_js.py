"""Python 版 API と JS ローカル API（static/local/*.js）の出力が一致することを確認する。
詳細は tests/js/parity.py。Node が無ければスキップ。"""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "js"))

node = shutil.which("node")


@pytest.mark.skipif(node is None, reason="node がありません")
def test_python_and_js_api_parity():
    import parity

    n, diffs = parity.run_parity(node)
    assert n > 300, "比較した応答数が少なすぎる"
    assert not diffs, "\n".join(diffs[:30])


@pytest.mark.skipif(node is None, reason="node がありません")
def test_js_unit_tests():
    import subprocess

    root = Path(__file__).resolve().parent.parent
    p = subprocess.run([node, "--test", "tests/js/"], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
