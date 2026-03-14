#!/usr/bin/env bash
set -euo pipefail

# secrets-restore.sh -- Restore sensitive config files from .secrets/ backup
# Usage: Run after clone or after secrets-remove

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="$ROOT/.secrets"

if [[ ! -d "$VAULT" ]]; then
    echo "[error] .secrets/ directory not found."
    echo "        Run secrets-remove.sh first or place backup manually."
    exit 1
fi

echo "[secrets-restore] Restoring sensitive files from $VAULT"
echo

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
    if [[ -f "$VAULT/$f" ]]; then
        mkdir -p "$ROOT/$(dirname "$f")"
        mv -f "$VAULT/$f" "$ROOT/$(dirname "$f")/"
        echo "  [ok] $f"
        ((COUNT++)) || true
    fi
done

if [[ -d "$VAULT/config/memory" ]]; then
    mkdir -p "$ROOT/config/memory"
    cp -a "$VAULT/config/memory/." "$ROOT/config/memory/"
    rm -rf "$VAULT/config/memory"
    echo "  [ok] config/memory/ (entire directory)"
    ((COUNT++)) || true
fi

if [[ -z "$(ls -A "$VAULT" 2>/dev/null)" ]]; then
    rm -rf "$VAULT"
    echo
    echo "  .secrets/ directory cleaned up."
fi

echo
echo "[done] Restored $COUNT sensitive items."
