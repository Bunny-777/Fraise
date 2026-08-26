#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 EC2 box (t3.micro) to run the Fraise backend.
# Usage:  ./setup.sh            (run as ubuntu, from anywhere)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Dhruv-kys/Fraise}"
APP_DIR="/home/ubuntu/Fraise"

echo "==> Detecting public IP for the sslip.io hostname"
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org)"
HOSTNAME="${PUBLIC_IP//./-}.sslip.io"
echo "    IP=$PUBLIC_IP  ->  $HOSTNAME"

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl debian-keyring \
    debian-archive-keyring apt-transport-https ca-certificates

echo "==> Adding 2 GB swap (t3.micro has 1 GB RAM; Whisper spikes past it)"
if ! sudo swapon --show | grep -q /swapfile; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    # Prefer RAM, but let swap absorb transcription spikes rather than OOM-killing.
    echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-fraise.conf >/dev/null
    sudo sysctl -q -p /etc/sysctl.d/99-fraise.conf
    echo "    swap enabled"
else
    echo "    swap already present, skipping"
fi

echo "==> Cloning / updating the repo"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

echo "==> Building the Python environment"
cd "$APP_DIR/backend"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip -q
# No build cache: pip's cache alone can exhaust a t3.micro's disk headroom.
./.venv/bin/pip install --no-cache-dir -r requirements.txt

echo "==> Installing Caddy (TLS termination + WebSocket proxy)"
if ! command -v caddy >/dev/null; then
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq caddy
fi

echo "==> Writing Caddy config for $HOSTNAME"
sudo sed "s/<IP-WITH-DASHES>.sslip.io/$HOSTNAME/" "$APP_DIR/deploy/Caddyfile" \
    | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl reload caddy || sudo systemctl restart caddy

echo "==> Installing the backend service"
sudo cp "$APP_DIR/deploy/fraise.service" /etc/systemd/system/fraise.service
sudo systemctl daemon-reload
sudo systemctl enable fraise

if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" <<ENV
DEEPGRAM_API_KEY=
GROQ_API_KEY=
TAVILY_API_KEY=
CORS_ORIGINS=https://fraise.vercel.app
ENV
    echo
    echo "!! Created $APP_DIR/.env with EMPTY keys."
    echo "!! Fill it in, then:  sudo systemctl start fraise"
else
    sudo systemctl restart fraise
    echo "    service restarted"
fi

echo
echo "==================================================================="
echo " Backend URL : https://$HOSTNAME"
echo " Health      : curl https://$HOSTNAME/health"
echo " Set in Vercel:"
echo "   VITE_BACKEND_WS_URL = wss://$HOSTNAME/ws"
echo " Logs        : journalctl -u fraise -f"
echo "==================================================================="
