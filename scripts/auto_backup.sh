#!/bin/bash
# Auto backup ch3-planning to GitHub
# Usage: called by cron, or run manually: bash scripts/auto_backup.sh

REPO_DIR="/root/autodl-tmp/ch3-planning"
LOG_FILE="$REPO_DIR/data/backup.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

cd "$REPO_DIR" || exit 1

# Check if there are any changes
if [[ -z $(git status --porcelain) ]]; then
    # No changes, skip silently
    exit 0
fi

# Stage all changes
git add -A

# Count changed files
CHANGED=$(git diff --cached --numstat | wc -l)

# Commit
git commit -m "auto-backup: $TIMESTAMP ($CHANGED files changed)" --quiet

if [ $? -eq 0 ]; then
    # Push
    git push origin main --quiet 2>>"$LOG_FILE"
    if [ $? -eq 0 ]; then
        echo "[$TIMESTAMP] OK: $CHANGED files pushed" >> "$LOG_FILE"
    else
        echo "[$TIMESTAMP] PUSH FAILED" >> "$LOG_FILE"
    fi
else
    echo "[$TIMESTAMP] COMMIT FAILED" >> "$LOG_FILE"
fi
