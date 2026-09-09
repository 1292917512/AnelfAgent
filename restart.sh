#!/usr/bin/env bash
# AnelfAgent 一键重启脚本

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "🛑 关闭旧进程..."
# 优先 PID 文件精准终止（实例守卫登记，本项目专属，绝不误杀其他项目）
PID_FILE="$ROOT/logs/anelf.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    OLD_PID="$(cat "$PID_FILE")"
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 10); do
        kill -0 "$OLD_PID" 2>/dev/null || break
        sleep 0.5
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
fi
# 兜底：按项目目录限定匹配（旧版 "python.*launch" 会误杀其他项目进程）
pkill -9 -f "$ROOT/.*launch" 2>/dev/null || true
sleep 1

echo "🚀 后台启动 AnelfAgent..."
nohup ./start.sh > /tmp/anelf_startup.log 2>&1 &
echo "📝 日志输出: /tmp/anelf_startup.log"
echo "✅ 重启指令已发出"
