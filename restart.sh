#!/usr/bin/env bash
# 安全关停成功后才启动；关停超时绝不升级强杀。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
bash "$ROOT/stop.sh"
mkdir -p "$ROOT/logs"
echo "🚀 后台启动 AnelfAgent..."
nohup ./start.sh > "$ROOT/logs/startup.log" 2>&1 &
echo "📝 日志输出: $ROOT/logs/startup.log"
