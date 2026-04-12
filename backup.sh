#!/bin/bash

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/xagent-backups"
KEEP_DAYS=30
DATE=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"

# ─── SQLite backup (safe while running) ───────────────────────────────

if [ -f "$APP_DIR/data/xagent.db" ]; then
    sqlite3 "$APP_DIR/data/xagent.db" ".backup $BACKUP_DIR/xagent-$DATE.db"
    gzip "$BACKUP_DIR/xagent-$DATE.db"
    ln -sf "$BACKUP_DIR/xagent-$DATE.db.gz" "$BACKUP_DIR/xagent-latest.db.gz"
    echo "$(date): DB backup created — xagent-$DATE.db.gz"
else
    echo "$(date): No database file found at $APP_DIR/data/xagent.db"
fi

# ─── Backup .env ──────────────────────────────────────────────────────

if [ -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env" "$BACKUP_DIR/env-$DATE.backup"
fi

# ─── Prune old backups ────────────────────────────────────────────────

find "$BACKUP_DIR" -name "*.db.gz" -mtime +$KEEP_DAYS -delete
find "$BACKUP_DIR" -name "*.backup" -mtime +$KEEP_DAYS -delete

BACKUP_COUNT=$(ls "$BACKUP_DIR"/*.db.gz 2>/dev/null | wc -l)
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)

echo "$(date): Total backups: $BACKUP_COUNT ($BACKUP_SIZE)"
