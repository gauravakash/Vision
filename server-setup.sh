#!/bin/bash
set -e

echo "================================================================"
echo "  X Agent — Fresh VPS Setup (Ubuntu 22.04)"
echo "================================================================"

# Must be root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root"
    exit 1
fi

# System update
echo "[1/7] System update..."
apt-get update && apt-get upgrade -y

# Essential tools
echo "[2/7] Installing tools..."
apt-get install -y \
    htop ncdu git curl wget sqlite3 nano unzip ufw fail2ban

# Docker
echo "[3/7] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl start docker
    systemctl enable docker
else
    echo "  Docker already installed"
fi

# Firewall
echo "[4/7] Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
ufw --force enable

# Fail2ban for SSH brute-force protection
echo "[5/7] Configuring fail2ban..."
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
EOF
systemctl restart fail2ban
systemctl enable fail2ban

# Swap file (2GB — important for 4GB RAM VPS)
echo "[6/7] Setting up swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "  Swap file created (2GB)"
else
    echo "  Swap already exists"
fi

# Increase file descriptor limits (needed for Playwright)
echo "[7/7] Tuning system limits..."
grep -q "nofile 65536" /etc/security/limits.conf || {
    echo "* soft nofile 65536" >> /etc/security/limits.conf
    echo "* hard nofile 65536" >> /etc/security/limits.conf
}

echo ""
echo "================================================================"
echo "  Server setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Upload code to /opt/xagent/"
echo "    2. cp .env.example .env && nano .env"
echo "    3. bash deploy.sh"
echo "================================================================"
