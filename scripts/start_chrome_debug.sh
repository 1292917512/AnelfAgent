#!/bin/bash
# 以远程调试模式启动 Chrome，供 chrome-devtools-mcp（Anelf MCP: chrome-devtools）接管。
#
# 注意：
# - Chrome 136+ 不允许默认用户目录开启 --remote-debugging-port，
#   因此使用独立 profile 目录 ~/.anelf/chrome-profile。
# - 首次启动后在该窗口中登录需要的网站，登录态会持久保留。
# - 关掉所有该 profile 的 Chrome 窗口后，重新运行本脚本即可再次接管。

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR="$HOME/.anelf/chrome-profile"

if [ ! -x "$CHROME" ]; then
  echo "未找到 Chrome: $CHROME" >&2
  exit 1
fi

mkdir -p "$PROFILE_DIR"

# 已在运行则直接复用（9222 端口被占用说明调试实例已存在）
if curl -s --max-time 1 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
  echo "Chrome 调试实例已在运行: http://127.0.0.1:9222"
  exit 0
fi

echo "启动 Chrome（调试端口 9222，profile: $PROFILE_DIR）..."
exec "$CHROME" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check
