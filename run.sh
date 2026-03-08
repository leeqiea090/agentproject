#!/bin/bash
# ============================================================
# agentproject 一键启动脚本
# 用法：bash run.sh
# ============================================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="$PROJECT_DIR/.venv"

echo "================================================"
echo "  招投标 AI Agent 服务 - 一键启动"
echo "================================================"

# 1. 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi
echo "[1/4] Python 版本: $(python3 --version)"

# 2. 创建虚拟环境（如果不存在）
if [ ! -d "$VENV_DIR" ]; then
    echo "[2/4] 创建虚拟环境 .venv ..."
    python3 -m venv "$VENV_DIR"
else
    echo "[2/4] 虚拟环境已存在，跳过创建"
fi

# 3. 激活虚拟环境并安装依赖
source "$VENV_DIR/bin/activate"
echo "[3/4] 安装/更新依赖 ..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# 4. 启动服务
echo "[4/4] 启动服务，访问地址：http://localhost:8000"
echo "================================================"
echo "  按 Ctrl+C 停止服务"
echo "================================================"

python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

