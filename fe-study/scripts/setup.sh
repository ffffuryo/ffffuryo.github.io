#!/usr/bin/env bash
# FE Study 初期セットアップ: venv 作成 → 依存インストール → 問題データ取り込み
#
# 使い方:  bash scripts/setup.sh
#   - 取り込み（python -m importer）は IPA から PDF を取得するため数分〜十数分かかる。
#     失敗しても全体は止めず、再実行方法を案内して終了する。
#   - 2 回目以降は venv と依存の再インストールだけ行う（冪等）。

set -euo pipefail

# スクリプト自身の場所から fe-study ディレクトリを解決（カレントディレクトリに依存しない）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"

cd "${APP_DIR}"

# Python 3 の確認
if ! command -v python3 >/dev/null 2>&1; then
    echo "エラー: python3 が見つかりません。Python 3.10 以上をインストールしてください。" >&2
    exit 1
fi

echo "==> 作業ディレクトリ: ${APP_DIR}"

# 1. venv 作成（既にあれば再利用）
if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "==> 仮想環境を作成します: ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
else
    echo "==> 既存の仮想環境を使用します: ${VENV_DIR}"
fi

PY="${VENV_DIR}/bin/python"

# 2. 依存インストール
echo "==> 依存パッケージをインストールします（requirements.txt）"
"${PY}" -m pip install --upgrade pip
"${PY}" -m pip install -r requirements.txt

# 3. 問題データの取り込み（失敗しても続行）
echo "==> 問題データを IPA から取り込みます（初回のみ・数分〜十数分）"
if "${PY}" -m importer; then
    echo "==> 問題データの取り込みが完了しました。"
else
    echo ""
    echo "警告: 問題データの取り込みに失敗しました（ネットワーク不通などが原因のことがあります）。"
    echo "      アプリ本体は起動できます。取り込みは後から次のコマンドで再実行してください:"
    echo ""
    echo "        cd \"${APP_DIR}\" && .venv/bin/python -m importer"
    echo ""
fi

echo ""
echo "セットアップ完了。起動するには:"
echo "    bash scripts/run.sh"
