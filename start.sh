#!/usr/bin/env bash
# AnelfTools 启动脚本 (macOS / Linux)

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

echo ""
echo "  ┌─────────────────────────────────────┐"
echo "  │          AnelfAgent                  │"
echo "  └─────────────────────────────────────┘"
echo ""

# 检测 uv（优先）或 python
if command -v uv &>/dev/null; then
    RUN_CMD="uv run python"
    echo "  [运行器] $(uv --version 2>&1 | head -1)"

    echo "  [环境]   正在同步 Python 依赖..."
    if uv sync --quiet; then
        echo "  [环境]   Python 依赖已就绪"
    else
        echo "  [警告]   uv sync 失败，将使用当前环境继续"
    fi
elif command -v python3 &>/dev/null; then
    RUN_CMD="python3"
    echo "  [运行器] $(python3 --version 2>&1)"
elif command -v python &>/dev/null; then
    RUN_CMD="python"
    echo "  [运行器] $(python --version 2>&1)"
else
    echo "  [错误] 未找到 uv 或 python，请先安装运行环境"
    echo "         uv 安装: https://github.com/astral-sh/uv"
    exit 1
fi

# 同步前端依赖
FRONTEND_DIR="$ROOT/web/frontend"
if [ -f "$FRONTEND_DIR/package.json" ] && command -v npm &>/dev/null; then
    echo "  [环境]   正在同步前端依赖..."
    if npm install --prefix "$FRONTEND_DIR" --silent 2>/dev/null; then
        echo "  [环境]   前端依赖已就绪"
    else
        echo "  [警告]   npm install 失败，前端功能可能异常"
    fi
fi

echo ""
echo "  [目录] $ROOT"
echo ""
echo "  WebUI 地址: http://127.0.0.1:8092/webui/"
echo "  按 Ctrl+C 停止服务"
echo "  ─────────────────────────────────────────"
echo ""

$RUN_CMD launch.py "$@"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  [!] 服务异常退出，错误码: $EXIT_CODE"
fi
