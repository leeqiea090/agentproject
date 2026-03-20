#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

resolve_python_bin() {
    local candidate=""
    local env_name=""

    if [[ -n "${PYTHON_BIN:-}" ]]; then
        candidate="$PYTHON_BIN"
    elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        candidate="${VIRTUAL_ENV}/bin/python"
    else
        if [[ -n "${VIRTUAL_ENV:-}" ]]; then
            env_name="$(basename "$VIRTUAL_ENV")"
            if [[ -x "${ROOT_DIR}/${env_name}/bin/python" ]]; then
                candidate="${ROOT_DIR}/${env_name}/bin/python"
            fi
        fi

        if [[ -n "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi

        for candidate in \
            "${ROOT_DIR}/.venv/bin/python" \
            "${ROOT_DIR}/.venv-test/bin/python"
        do
            if [[ -x "$candidate" ]]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        done

        if command -v python >/dev/null 2>&1; then
            candidate="$(command -v python)"
        elif command -v python3 >/dev/null 2>&1; then
            candidate="$(command -v python3)"
        fi
    fi

    if [[ -z "$candidate" ]]; then
        echo "未找到可用的 Python 解释器。" >&2
        return 1
    fi

    if [[ "$candidate" == */* && ! -x "$candidate" ]]; then
        echo "PYTHON_BIN 不可执行: $candidate" >&2
        return 1
    fi

    printf '%s\n' "$candidate"
}

is_virtualenv_python() {
    local python_bin="$1"
    "$python_bin" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)'
}

PYTHON_BIN="$(resolve_python_bin)"

if ! is_virtualenv_python "$PYTHON_BIN"; then
    cat >&2 <<'EOF'
构建脚本必须使用虚拟环境中的 Python，不能直接使用 macOS/Homebrew 的系统 Python。

请执行以下任一方式：
  1. 使用已有虚拟环境：
     PYTHON_BIN=.venv/bin/python ./scripts/build_macos_app.sh
  2. 重新创建虚拟环境后再构建：
     python3 -m venv .venv
     source .venv/bin/activate
     PYTHON_BIN=.venv/bin/python ./scripts/build_macos_app.sh
EOF
    exit 1
fi

echo "使用 Python: $PYTHON_BIN"

"$PYTHON_BIN" -m pip install -r requirements-desktop.txt
"$PYTHON_BIN" -m PyInstaller --noconfirm BidAgent.spec

echo
echo "构建完成:"
echo "  $ROOT_DIR/dist/BidAgent.app"
echo
echo "可选配置文件位置:"
echo "  ~/Library/Application Support/BidAgent/.env"
