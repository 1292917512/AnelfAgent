#!/usr/bin/env bash
# 安全停止项目；超时保留进程，原生数据库写入不能被自动强杀。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
echo "🛑 正在安全关闭；未完成同步将在下次启动重试..."
if command -v uv >/dev/null 2>&1; then
    uv run --no-sync python -m core.instance_guard --timeout "${ANELF_STOP_TIMEOUT_SECONDS:-600}"
else
    python3 -m core.instance_guard --timeout "${ANELF_STOP_TIMEOUT_SECONDS:-600}"
fi
echo "✅ 进程已退出"
