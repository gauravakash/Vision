#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════╗"
echo "║     X Agent — Deploy Script          ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# Configuration
DOMAIN="${DOMAIN:-}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="/opt/xagent-backups"

# ─── Step 1: Preflight checks ─────────────────────────────────────────

echo -e "${YELLOW}[1/8] Preflight checks...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (or sudo)${NC}"
    exit 1
fi

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker not installed. Run server-setup.sh first.${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}Docker Compose V2 not available. Update Docker.${NC}"
    exit 1
fi

RAM_MB=$(free -m | awk '/Mem/{print $2}')
if [ "$RAM_MB" -lt 2048 ]; then
    echo -e "${YELLOW}  Warning: ${RAM_MB}MB RAM detected (recommend 4GB)${NC}"
fi

echo -e "${GREEN}  Preflight passed${NC}"

# ─── Step 2: Directories ──────────────────────────────────────────────

echo -e "${YELLOW}[2/8] Setting up directories...${NC}"

mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/logs"
mkdir -p "$APP_DIR/certbot/conf"
mkdir -p "$APP_DIR/certbot/www"
mkdir -p "$BACKUP_DIR"

echo -e "${GREEN}  Directories ready${NC}"

# ─── Step 3: Environment file ─────────────────────────────────────────

echo -e "${YELLOW}[3/8] Checking environment...${NC}"

if [ ! -f "$APP_DIR/.env" ]; then
    echo -e "${YELLOW}  Creating .env from template...${NC}"

    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    else
        cat > "$APP_DIR/.env" << 'ENVEOF'
# ─── REQUIRED ─────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-REPLACE-ME
COOKIE_ENCRYPT_KEY=REPLACE_WITH_FERNET_KEY
SECRET_KEY=REPLACE_WITH_64_HEX_CHARS

# ─── Optional ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# ─── Settings ─────────────────────────────────────────
DEBUG=false
LOG_LEVEL=INFO
DATABASE_URL=sqlite+aiosqlite:///./data/xagent.db
ANTHROPIC_MODEL=claude-sonnet-4-20250514
MAX_POSTS_PER_ACCOUNT_DAY=8
MIN_GAP_BETWEEN_POSTS_MIN=45
MONTHLY_COST_LIMIT_USD=25.0
COST_ALERT_THRESHOLD_USD=20.0
ENVEOF
    fi

    echo -e "${RED}"
    echo "  ╔═══════════════════════════════════════════════╗"
    echo "  ║  .env created — you MUST fill in your keys!  ║"
    echo "  ║                                               ║"
    echo "  ║  Edit:  nano $APP_DIR/.env          ║"
    echo "  ║  Then run this script again.                  ║"
    echo "  ╚═══════════════════════════════════════════════╝"
    echo -e "${NC}"
    exit 1
fi

# Validate critical keys
source "$APP_DIR/.env"
if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "sk-ant-REPLACE-ME" ]; then
    echo -e "${RED}  ERROR: ANTHROPIC_API_KEY not set in .env${NC}"
    exit 1
fi
if [ -z "$COOKIE_ENCRYPT_KEY" ] || [ "$COOKIE_ENCRYPT_KEY" = "REPLACE_WITH_FERNET_KEY" ]; then
    echo -e "${RED}  ERROR: COOKIE_ENCRYPT_KEY not set in .env${NC}"
    echo "  Generate with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    exit 1
fi
if [ -z "$SECRET_KEY" ] || [ "$SECRET_KEY" = "REPLACE_WITH_64_HEX_CHARS" ]; then
    echo -e "${RED}  ERROR: SECRET_KEY not set in .env${NC}"
    echo "  Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    exit 1
fi

echo -e "${GREEN}  Environment validated${NC}"

# ─── Step 4: SSL Certificate ──────────────────────────────────────────

if [ -n "$DOMAIN" ]; then
    echo -e "${YELLOW}[4/8] SSL certificate for $DOMAIN...${NC}"

    if [ ! -d "$APP_DIR/certbot/conf/live/$DOMAIN" ]; then
        # Stop anything on port 80
        docker compose down 2>/dev/null || true

        docker run --rm \
            -v "$APP_DIR/certbot/conf:/etc/letsencrypt" \
            -v "$APP_DIR/certbot/www:/var/www/certbot" \
            -p 80:80 \
            certbot/certbot certonly \
            --standalone \
            --email "admin@$DOMAIN" \
            --agree-tos \
            --no-eff-email \
            -d "$DOMAIN"

        echo -e "${GREEN}  SSL certificate obtained${NC}"
    else
        echo -e "${GREEN}  SSL certificate already exists${NC}"
    fi

    # Update nginx.conf with actual domain
    sed -i "s/YOUR_DOMAIN\.COM/$DOMAIN/g" "$APP_DIR/nginx.conf"
else
    echo -e "${YELLOW}[4/8] No DOMAIN set — skipping SSL${NC}"
    echo "  Platform will be accessible on http://YOUR_IP:3000"
    echo "  Set DOMAIN=yourdomain.com to enable HTTPS"
fi

# ─── Step 5: Backup existing database ─────────────────────────────────

echo -e "${YELLOW}[5/8] Pre-deploy backup...${NC}"

if [ -f "$APP_DIR/data/xagent.db" ]; then
    DATE=$(date +%Y%m%d-%H%M%S)
    cp "$APP_DIR/data/xagent.db" "$BACKUP_DIR/xagent-predeploy-$DATE.db"
    echo -e "${GREEN}  Database backed up${NC}"
else
    echo "  No existing database (first deploy)"
fi

# ─── Step 6: Build containers ─────────────────────────────────────────

echo -e "${YELLOW}[6/8] Building containers (this takes 5-10 min first time)...${NC}"

cd "$APP_DIR"
docker compose build --no-cache

echo -e "${GREEN}  Build complete${NC}"

# ─── Step 7: Start services ───────────────────────────────────────────

echo -e "${YELLOW}[7/8] Starting services...${NC}"

docker compose up -d

# Wait for backend health
echo -n "  Waiting for backend health"
MAX_WAIT=120
WAITED=0
until curl -sf http://localhost:8000/health > /dev/null 2>&1; do
    sleep 5
    WAITED=$((WAITED + 5))
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo ""
        echo -e "${RED}  Backend failed to start within ${MAX_WAIT}s${NC}"
        echo "  Check logs: docker compose logs backend"
        exit 1
    fi
    echo -n "."
done
echo ""

echo -e "${GREEN}  All services running${NC}"

# ─── Step 8: Post-deploy ──────────────────────────────────────────────

echo -e "${YELLOW}[8/8] Post-deploy tasks...${NC}"

# Seed default desks
curl -sf -X POST http://localhost:8000/api/desks/seed > /dev/null 2>&1 \
    && echo -e "  ${GREEN}Desks seeded${NC}" \
    || echo "  Desks already exist (skipped)"

# Setup cron jobs
CRON_MARKER="# xagent-managed"

# Remove old xagent crons
crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab - 2>/dev/null || true

# Add fresh crons
(crontab -l 2>/dev/null; cat << CRONEOF
0 3 * * * $APP_DIR/backup.sh >> $APP_DIR/logs/backup.log 2>&1 $CRON_MARKER
0 */12 * * * cd $APP_DIR && docker compose exec -T certbot certbot renew --quiet 2>&1 $CRON_MARKER
0 4 * * 0 docker system prune -f >> $APP_DIR/logs/docker-prune.log 2>&1 $CRON_MARKER
CRONEOF
) | crontab -

echo -e "  ${GREEN}Cron jobs configured (backup 3AM, cert-renew 12h, prune weekly)${NC}"

# ─── Done ──────────────────────────────────────────────────────────────

IP=$(curl -sf ifconfig.me 2>/dev/null || echo "YOUR_VPS_IP")

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗"
echo "║        X Agent Deployed Successfully!            ║"
echo "╚══════════════════════════════════════════════════╝${NC}"
echo ""

if [ -n "$DOMAIN" ]; then
    echo "  Dashboard:  https://$DOMAIN"
    echo "  API Docs:   https://$DOMAIN/docs"
    echo "  Health:     https://$DOMAIN/health"
else
    echo "  Dashboard:  http://$IP:3000"
    echo "  API (raw):  http://$IP:8000/docs"
    echo "  Health:     http://$IP:8000/health"
fi

echo ""
echo "  Useful commands:"
echo "    docker compose logs -f backend    # live backend logs"
echo "    docker compose logs -f frontend   # live frontend logs"
echo "    docker compose restart            # restart all"
echo "    docker compose down               # stop all"
echo "    bash update.sh                    # pull + rebuild + restart"
echo "    bash backup.sh                    # manual DB backup"
echo ""
