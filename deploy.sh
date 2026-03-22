#!/bin/bash
# deploy.sh — Run this on your Hostinger VPS to deploy Opaux
# Usage: bash deploy.sh
set -e

DOMAIN="opaux.com"
APP_DIR="/opt/opaux"
REPO="https://github.com/faykgordon-sudo/opaux.git"

echo "=== Opaux Deploy Script ==="
echo "Domain: $DOMAIN"

# ── 1. System deps ─────────────────────────────────────────────────────────────
echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y --no-install-recommends git curl ufw

# Docker
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
# Docker Compose v2
if ! docker compose version &>/dev/null; then
    apt-get install -y docker-compose-plugin
fi

# ── 2. Firewall ────────────────────────────────────────────────────────────────
echo "[2/7] Configuring firewall..."
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP
ufw allow 443/tcp  # HTTPS
ufw --force enable

# ── 3. Clone or pull repo ──────────────────────────────────────────────────────
echo "[3/7] Getting application code..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git pull
else
    git clone "$REPO" "$APP_DIR"
    cd "$APP_DIR"
fi

# ── 4. .env file ──────────────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    echo "[4/7] Creating .env from .env.example — EDIT THIS BEFORE CONTINUING"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    # Generate a secure secret key
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET/" "$APP_DIR/.env"
    echo ""
    echo "  !! STOP: Edit $APP_DIR/.env and add your API keys, then re-run this script !!"
    echo "     nano $APP_DIR/.env"
    exit 1
else
    echo "[4/7] .env already exists — skipping."
fi

# ── 5. First-run: get SSL cert (HTTP-only nginx first) ─────────────────────────
echo "[5/7] Obtaining SSL certificate..."
mkdir -p "$APP_DIR/data/certbot/www"
mkdir -p "$APP_DIR/data/certbot/conf"

# Temporarily use HTTP-only nginx to pass ACME challenge
if [ ! -d "$APP_DIR/data/certbot/conf/live/$DOMAIN" ]; then
    # Start nginx in HTTP-only mode
    docker run --rm -d --name tmp-nginx \
        -v "$APP_DIR/nginx/nginx-http-only.conf:/etc/nginx/nginx.conf:ro" \
        -v "$APP_DIR/data/certbot/www:/var/www/certbot" \
        -p 80:80 nginx:alpine 2>/dev/null || true

    sleep 2

    docker run --rm \
        -v "$APP_DIR/data/certbot/www:/var/www/certbot" \
        -v "$APP_DIR/data/certbot/conf:/etc/letsencrypt" \
        certbot/certbot certonly \
        --webroot -w /var/www/certbot \
        --email "admin@$DOMAIN" \
        --agree-tos --no-eff-email \
        -d "$DOMAIN" -d "www.$DOMAIN"

    docker stop tmp-nginx 2>/dev/null || true
    echo "  SSL certificate obtained!"
else
    echo "  Certificate already exists — skipping."
fi

# ── 6. Build and start ─────────────────────────────────────────────────────────
echo "[6/7] Building and starting containers..."
cd "$APP_DIR"
docker compose pull nginx certbot
docker compose build app
docker compose up -d

# ── 7. Health check ────────────────────────────────────────────────────────────
echo "[7/7] Health check..."
sleep 5
if curl -sf "https://$DOMAIN/login" -o /dev/null; then
    echo ""
    echo "  ✓ Opaux is live at https://$DOMAIN"
else
    echo "  ✗ Health check failed. Check logs: docker compose logs"
fi

echo ""
echo "=== Deploy complete ==="
echo "Useful commands:"
echo "  docker compose logs -f        # live logs"
echo "  docker compose restart app    # restart app"
echo "  docker compose down           # stop everything"
echo "  docker compose exec app bash  # shell into container"
