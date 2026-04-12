#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/xagent-backups"
DATE=$(date +%Y%m%d-%H%M%S)

echo -e "${YELLOW}X Agent — Update${NC}"
echo ""

# ─── Pre-update backup ────────────────────────────────────────────────

echo "[1/5] Backing up database..."
if [ -f "$APP_DIR/data/xagent.db" ]; then
    mkdir -p "$BACKUP_DIR"
    sqlite3 "$APP_DIR/data/xagent.db" ".backup $BACKUP_DIR/xagent-preupdate-$DATE.db"
    echo -e "${GREEN}  Backed up to xagent-preupdate-$DATE.db${NC}"
else
    echo "  No database to back up"
fi

# ─── Pull latest code ─────────────────────────────────────────────────

echo "[2/5] Pulling latest code..."
cd "$APP_DIR"
if [ -d ".git" ]; then
    git pull origin main
    echo -e "${GREEN}  Code updated${NC}"
else
    echo "  Not a git repo — skipping pull (upload code manually)"
fi

# ─── Rebuild ───────────────────────────────────────────────────────────

echo "[3/5] Rebuilding containers..."
docker compose build backend frontend

# ─── Rolling restart ───────────────────────────────────────────────────

echo "[4/5] Restarting services..."
docker compose up -d --no-deps backend
sleep 5
docker compose up -d --no-deps frontend

if [ -n "$(docker compose ps --services | grep nginx)" ]; then
    docker compose exec -T nginx nginx -s reload 2>/dev/null || true
fi

# ─── Health check ──────────────────────────────────────────────────────

echo -n "[5/5] Waiting for health"
MAX_WAIT=60
WAITED=0
until curl -sf http://localhost:8000/health > /dev/null 2>&1; do
    sleep 3
    WAITED=$((WAITED + 3))
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo ""
        echo -e "${RED}  Health check failed after ${MAX_WAIT}s!${NC}"
        echo -e "${YELLOW}  Rolling back database...${NC}"
        if [ -f "$BACKUP_DIR/xagent-preupdate-$DATE.db" ]; then
            cp "$BACKUP_DIR/xagent-preupdate-$DATE.db" "$APP_DIR/data/xagent.db"
        fi
        docker compose restart backend
        echo -e "${RED}  Update failed — check: docker compose logs backend${NC}"
        exit 1
    fi
    echo -n "."
done
echo ""

VERSION=$(curl -sf http://localhost:8000/health | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version","unknown"))' 2>/dev/null || echo "unknown")

echo ""
echo -e "${GREEN}Update complete! Running version: $VERSION${NC}"
