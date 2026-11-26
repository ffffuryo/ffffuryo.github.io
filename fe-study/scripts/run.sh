#!/usr/bin/env bash
# FE Study 起動スクリプト。venv があれば有効化して `python -m app` を実行する。
#
# 使い方:  bash scripts/run.sh
# 既定は 127.0.0.1:8000 で listen する（環境変数 FE_HOST / FE_PORT / FE_DB で変更可）。
# 外部へ公開するには Tailscale Serve を使うこと（docs/DEPLOY_TAILSCALE.md 参照）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${APP_DIR}/.venv"

cd "${APP_DIR}"

if [ -f "${VENV_DIR}/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
else
    echo "注意: .venv が見つからないため、システムの python3 で起動します。" >&2
    echo "      先に bash scripts/setup.sh の実行を推奨します。" >&2
fi

PYTHON="${VENV_DIR}/bin/python"
if [ ! -x "${PYTHON}" ]; then
    PYTHON="$(command -v python3 || true)"
fi
if [ -z "${PYTHON}" ]; then
    echo "エラー: python が見つかりません。" >&2
    exit 1
fi

echo "==> FE Study を起動します (http://${FE_HOST:-127.0.0.1}:${FE_PORT:-8000}/)"
exec "${PYTHON}" -m app
