#!/usr/bin/env bash
# AnelfAgent 一键重启脚本

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "🛑 关闭旧进程..."
pkill -9 -f "python.*launch" 2>/dev/null || true
sleep 1

echo "🚀 后台启动 AnelfAgent..."
nohup ./start.sh > /tmp/anelf_startup.log 2>&1 &
echo "📝 日志输出: /tmp/anelf_startup.log"
echo "✅ 重启指令已发出"
