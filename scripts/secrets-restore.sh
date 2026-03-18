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
git pull --ff-only 2>/dev/null || git pull --rebase
cd "$ROOT"
echo

echo "[restore] Copying files to project directory..."
echo

COUNT=0

FILES=(
    "config/llm_clients.json"
    "config/mcp_servers.json"
    "config/app_config.json"
    "config/mind_config.json"
    "config/heartbeat.json"
    "config/webui.json"
    "config/personas/mengli.json"
    "channels/telegram/channel_config.json"
    "channels/qq/channel_config.json"
    "channels/feishu/channel_config.json"
)

for f in "${FILES[@]}"; do
    if [[ -f "$VAULT/$f" ]]; then
        mkdir -p "$ROOT/$(dirname "$f")"
        cp -f "$VAULT/$f" "$ROOT/$f"
        echo "  [ok] $f"
        ((COUNT++)) || true
    fi
done

if [[ -d "$VAULT/config/memory" ]]; then
    mkdir -p "$ROOT/config/memory"
    rsync -a --delete \
        --exclude='*.sqlite3-wal' \
        --exclude='*.sqlite3-shm' \
        "$VAULT/config/memory/" "$ROOT/config/memory/"
    echo "  [ok] config/memory/ (synced)"
    ((COUNT++)) || true
fi

if [[ -d "$VAULT/config/tasks" ]]; then
    mkdir -p "$ROOT/config/tasks"
    rsync -a --delete \
        "$VAULT/config/tasks/" "$ROOT/config/tasks/"
    echo "  [ok] config/tasks/ (synced)"
    ((COUNT++)) || true
fi

echo
echo "[done] Restored $COUNT items from private repo."
