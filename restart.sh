#!/usr/bin/env bash
# AnelfAgent 一键重启脚本

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "🛑 关闭旧进程..."
# 先停守护循环（start.sh 登记）：不先停它，杀掉 python 后会被立刻重新拉起，
# 且旧守护在 42 重启/崩溃退避窗口内唤醒会与本脚本拉起的新实例竞态互杀
GUARDIAN_PID_FILE="$ROOT/logs/anelf_guardian.pid"
if [ -f "$GUARDIAN_PID_FILE" ] && kill -0 "$(cat "$GUARDIAN_PID_FILE")" 2>/dev/null; then
    GUARDIAN_PID="$(cat "$GUARDIAN_PID_FILE")"
    kill "$GUARDIAN_PID" 2>/dev/null || true
    rm -f "$GUARDIAN_PID_FILE"
fi
# 优先 PID 文件精准终止（实例守卫登记，本项目专属，绝不误杀其他项目）
PID_FILE="$ROOT/logs/anelf.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    OLD_PID="$(cat "$PID_FILE")"
    kill "$OLD_PID" 2>/dev/null || true
    # 关停窗口对齐应用关停预算（45s）+ 余量：记忆兜底与 Lifecycle 逆序
    # drain 需要完整窗口，过短的强杀会让优雅关停设计名存实亡
    for _ in $(seq 1 100); do
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
