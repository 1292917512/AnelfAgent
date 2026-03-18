#!/usr/bin/env bash
set -euo pipefail

# secrets-backup.sh -- Sync personal files to .secrets/ and push to private repo
# Usage: ./scripts/secrets-backup.sh [commit message]

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="$ROOT/.secrets"

if [[ ! -d "$VAULT/.git" ]]; then
    echo "[error] .secrets/ is not a git repo. Initialize it first."
    exit 1
fi

echo "[backup] Syncing personal files to .secrets/"
echo

mkdir -p \
    "$VAULT/config/personas" \
    "$VAULT/config/memory" \
    "$VAULT/config/tasks" \
    "$VAULT/channels/telegram" \
    "$VAULT/channels/qq" \
    "$VAULT/channels/feishu" \
    "$VAULT/entities/web"

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
    "entities/web/config.json"
)

for f in "${FILES[@]}"; do
    if [[ -f "$ROOT/$f" ]]; then
        cp -f "$ROOT/$f" "$VAULT/$(dirname "$f")/"
        echo "  [ok] $f"
        ((COUNT++)) || true
    fi
done

# config/memory/ (SQLite data + MD notes)
if [[ -d "$ROOT/config/memory" ]]; then
    rsync -a --delete \
        --exclude='*.sqlite3-wal' \
        --exclude='*.sqlite3-shm' \
        "$ROOT/config/memory/" "$VAULT/config/memory/"
    echo "  [ok] config/memory/ (synced)"
    ((COUNT++)) || true
fi

# config/tasks/ (personal task definitions)
if [[ -d "$ROOT/config/tasks" ]]; then
    rsync -a --delete \
        "$ROOT/config/tasks/" "$VAULT/config/tasks/"
    echo "  [ok] config/tasks/ (synced)"
    ((COUNT++)) || true
fi

echo
echo "[backup] $COUNT items synced. Pushing to private repo..."
echo

cd "$VAULT"
git add -A
if git diff --cached --quiet; then
    echo "[backup] No changes to commit."
else
    MSG="${1:-backup $(date '+%Y-%m-%d %H:%M')}"
    git commit -m "$MSG"
    git push
    echo
    echo "[done] Backup pushed to private repo."
fi
