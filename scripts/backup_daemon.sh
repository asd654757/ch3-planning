#!/bin/bash
# Auto-backup daemon for ch3-planning
# Runs the backup script every 30 minutes.
# Usage: nohup bash scripts/backup_daemon.sh &

REPO_DIR="/root/autodl-tmp/ch3-planning"
INTERVAL=1800  # 30 minutes

while true; do
    bash "$REPO_DIR/scripts/auto_backup.sh"
    sleep "$INTERVAL"
done
