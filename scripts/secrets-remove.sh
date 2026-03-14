#!/usr/bin/env bash
set -euo pipefail

# secrets-remove.sh -- Move sensitive config files to .secrets/ backup
# Usage: Run before making repo public or committing

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="$ROOT/.secrets"

echo "[secrets-remove] Moving sensitive files to $VAULT"
echo

mkdir -p \
    "$VAULT/config/personas" \
    "$VAULT/config/memory" \
    "$VAULT/channels/telegram" \
    "$VAULT/channels/qq" \
    "$VAULT/channels/feishu"

COUNT=0

FILES=(
    "config/llm_clients.json"
    "config/mcp_servers.json"
    "config/app_config.json"
    "config/memory.md"
    "config/personas/mengli.json"
    "channels/telegram/channel_config.json"
    "channels/qq/channel_config.json"
    "channels/feishu/channel_config.json"
)

for f in "${FILES[@]}"; do
    if [[ -f "$ROOT/$f" ]]; then
        mv -f "$ROOT/$f" "$VAULT/$(dirname "$f")/"
        echo "  [ok] $f"
        ((COUNT++)) || true
    fi
done

if [[ -d "$ROOT/config/memory" ]]; then
    cp -a "$ROOT/config/memory/." "$VAULT/config/memory/"
    rm -rf "$ROOT/config/memory"
    echo "  [ok] config/memory/ (entire directory)"
    ((COUNT++)) || true
fi

echo
echo "[done] Moved $COUNT sensitive items to .secrets/"
echo "       Run scripts/secrets-restore.sh to restore."
