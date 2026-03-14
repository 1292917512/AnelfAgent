#!/usr/bin/env bash
set -euo pipefail

# secrets-restore.sh -- Pull latest from private repo and restore personal files
# Usage: ./scripts/secrets-restore.sh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="$ROOT/.secrets"

if [[ ! -d "$VAULT/.git" ]]; then
    echo "[error] .secrets/ is not a git repo. Initialize it first."
    exit 1
fi

echo "[restore] Pulling latest from private repo..."
cd "$VAULT"
git pull --ff-only
echo

mkdir -p \
    "$ROOT/config/personas" \
    "$ROOT/config/memory" \
    "$ROOT/channels/telegram" \
    "$ROOT/channels/qq" \
    "$ROOT/channels/feishu"

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
        cp -f "$VAULT/$f" "$ROOT/$(dirname "$f")/"
        echo "  [ok] $f"
        ((COUNT++)) || true
    else
        echo "  [skip] $f (not in backup)"
    fi
done

if [[ -d "$VAULT/config/memory" ]]; then
    rsync -a \
        "$VAULT/config/memory/" "$ROOT/config/memory/"
    echo "  [ok] config/memory/ (synced)"
    ((COUNT++)) || true
fi

echo
echo "[done] $COUNT items restored from .secrets/"
