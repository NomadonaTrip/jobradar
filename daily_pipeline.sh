#!/bin/bash
# Daily pipeline runner — called by cron (WSL2) or Windows Task Scheduler
set -euo pipefail

export PATH="/home/nomad/.local/bin:${PATH}"

PROJECT_DIR="/mnt/e/TOOLMAKER/AUTOMATIONS/RESUME_GEN"
PYTHON="${PROJECT_DIR}/.venv/bin/python"
LOG="${PROJECT_DIR}/pipeline.log"

echo "" >> "$LOG"
echo "=== Pipeline run: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

# Auto-import new customers from Google Drive (skips gracefully if no service_account.json)
if [ -f "${PROJECT_DIR}/service_account.json" ]; then
    "$PYTHON" "${PROJECT_DIR}/auto_import.py" >> "$LOG" 2>&1 || echo "[auto-import] failed" >> "$LOG"
else
    echo "[auto-import] skipped — no service_account.json" >> "$LOG"
fi

# Full pipeline for all active (non-expired) customers
"$PYTHON" "${PROJECT_DIR}/manage.py" run-all --tailor-limit 10 >> "$LOG" 2>&1

echo "[done] $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
