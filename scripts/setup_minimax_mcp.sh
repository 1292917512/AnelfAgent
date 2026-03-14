#!/usr/bin/env bash
# setup_minimax_mcp.sh — MiniMax Coding Plan MCP 环境安装脚本（macOS / Linux）
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC}   $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# 检测 uv
command -v uv &>/dev/null || error "未找到 uv，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
info "uv 已就绪: $(uv --version 2>&1 | head -1)"

# 安装依赖（含 minimax-coding-plan-mcp）
echo ""
echo "[*] 执行 uv sync 安装所有依赖..."
cd "$(dirname "$0")/.."
uv sync
info "安装完成，minimax-coding-plan-mcp 已就绪。"
echo "    确认 config/mcp_servers.json 中 minimax-coding-plan 的 enabled 为 true 即可。"
