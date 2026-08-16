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

# ── 崩溃守护 ─────────────────────────────────────────────────────────
# 崩溃退出码（128+信号号）自动重新拉起；SIGKILL=137 / SIGTERM=143 属
# 外部主动终止（含 restart.sh 替换），不自动重启。SIGBUS 编号平台不同。
if [ "$(uname -s)" = "Linux" ]; then
    CRASH_CODES="132 133 134 135 136 139"
    SIGBUS_CODE=135
else
    CRASH_CODES="132 133 134 136 138 139"
    SIGBUS_CODE=138
fi
MAX_CRASH_COUNT=5        # 连续崩溃达上限后停止自动拉起（防崩溃循环烧资源）
STABLE_RUN_SECONDS=600   # 运行超过该时长视为偶发崩溃，重置崩溃计数

_signal_name() {
    case $1 in
        132) echo "SIGILL" ;;
        133) echo "SIGTRAP" ;;
        134) echo "SIGABRT" ;;
        136) echo "SIGFPE" ;;
        "$SIGBUS_CODE") echo "SIGBUS" ;;
        139) echo "SIGSEGV" ;;
        *) echo "" ;;
    esac
}

_write_crash_state() {
    # 供重启后 AI 上下文注入（core/crash_report.py collect_previous_crash）与运维面板展示
    mkdir -p "$ROOT/logs"
    printf '{"exit_code": %d, "signal": "%s", "crashed_at": "%s", "crash_count": %d}\n' \
        "$1" "$2" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$3" > "$ROOT/logs/crash_state.json"
}

CRASH_COUNT=0
while true; do
    START_TS=$(date +%s)
    $RUN_CMD launch.py "$@"
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 42 ]; then
        echo ""
        echo "  [重启] 收到重启信号，3 秒后重新启动..."
        CRASH_COUNT=0
        sleep 3
        echo "  [重启] 正在重新启动..."
        echo ""
        continue
    fi

    IS_CRASH=0
    for code in $CRASH_CODES; do
        if [ $EXIT_CODE -eq $code ]; then
            IS_CRASH=1
            break
        fi
    done

    if [ $IS_CRASH -eq 1 ]; then
        END_TS=$(date +%s)
        if [ $((END_TS - START_TS)) -ge $STABLE_RUN_SECONDS ]; then
            CRASH_COUNT=1   # 长时间稳定运行后的单次崩溃：重新计数
        else
            CRASH_COUNT=$((CRASH_COUNT + 1))
        fi
        SIG_NAME=$(_signal_name "$EXIT_CODE")
        _write_crash_state "$EXIT_CODE" "$SIG_NAME" "$CRASH_COUNT"
        if [ $CRASH_COUNT -ge $MAX_CRASH_COUNT ]; then
            echo ""
            echo "  [!] 崩溃保护：连续 $CRASH_COUNT 次崩溃（${SIG_NAME:-退出码 $EXIT_CODE}），停止自动拉起"
            echo "      崩溃状态已写入 logs/crash_state.json，请排查后手动启动"
            break
        fi
        BACKOFF=$((5 * CRASH_COUNT))
        [ $BACKOFF -gt 60 ] && BACKOFF=60
        echo ""
        echo "  [!] 进程崩溃（${SIG_NAME:-未知信号}，退出码 $EXIT_CODE），${BACKOFF}s 后自动重新拉起（连续第 $CRASH_COUNT 次）"
        sleep $BACKOFF
        echo "  [重启] 正在重新启动..."
        echo ""
        continue
    fi

    if [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 143 ]; then
        echo ""
        echo "  [停止] 服务已停止（退出码 $EXIT_CODE）"
        break
    fi

    if [ $EXIT_CODE -eq 137 ]; then
        echo ""
        echo "  [停止] 进程被外部终止（SIGKILL），不自动重启"
        break
    fi

    if [ $EXIT_CODE -ne 0 ]; then
        echo ""
        echo "  [!] 服务异常退出，错误码: $EXIT_CODE"
    fi
    break
done
