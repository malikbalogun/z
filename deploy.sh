#!/bin/bash
set -e
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════╗"
echo "║   POLYMARKET REAL BOT — DEPLOY                ║"
echo "║   No simulation. Real trades. Real money.     ║"
echo "╚══════════════════════════════════════════════╝"
echo -e "${NC}"

DEPLOY_DIR="/opt/polymarket-bot"
SERVICE_NAME="polymarket-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Stop old
echo -e "${CYAN}[1/5] Stopping old bot...${NC}"
systemctl stop $SERVICE_NAME 2>/dev/null || true
pkill -f 'python.*main.py' 2>/dev/null || true
sleep 2
echo -e "${GREEN}[✓] Stopped${NC}"

# 2. Deploy files
echo -e "${CYAN}[2/5] Deploying...${NC}"
mkdir -p "$DEPLOY_DIR/templates"

# Preserve .env
[ -f "$DEPLOY_DIR/.env" ] && cp "$DEPLOY_DIR/.env" /tmp/_pm_env

cp "$SCRIPT_DIR/main.py" "$DEPLOY_DIR/main.py"
cp "$SCRIPT_DIR/trader.py" "$DEPLOY_DIR/trader.py"
cp "$SCRIPT_DIR/server.py" "$DEPLOY_DIR/server.py"
cp "$SCRIPT_DIR/requirements.txt" "$DEPLOY_DIR/requirements.txt"
cp "$SCRIPT_DIR/config.json.example" "$DEPLOY_DIR/config.json.example" 2>/dev/null || true
cp "$SCRIPT_DIR/templates/index.html" "$DEPLOY_DIR/templates/index.html"
cp "$SCRIPT_DIR/templates/login.html" "$DEPLOY_DIR/templates/login.html" 2>/dev/null || true
cp "$SCRIPT_DIR/templates/admin.html" "$DEPLOY_DIR/templates/admin.html" 2>/dev/null || true
rm -rf "$DEPLOY_DIR/bot"
cp -r "$SCRIPT_DIR/bot" "$DEPLOY_DIR/bot"

[ -f /tmp/_pm_env ] && cp /tmp/_pm_env "$DEPLOY_DIR/.env"
echo -e "${GREEN}[✓] Files deployed${NC}"

# 3. Install deps
echo -e "${CYAN}[3/5] Installing dependencies...${NC}"
cd "$DEPLOY_DIR"
if [ -d "venv" ]; then source venv/bin/activate
elif [ -d ".venv" ]; then source .venv/bin/activate
else python3 -m venv venv && source venv/bin/activate
fi
pip install -q -r requirements.txt 2>&1 | tail -3
echo -e "${GREEN}[✓] Dependencies ready${NC}"

# 4. Check .env
echo -e "${CYAN}[4/5] Checking config...${NC}"
if [ -f "$DEPLOY_DIR/.env" ]; then
    DRY=$(grep "^DRY_RUN" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)
    BET=$(grep "^DEFAULT_BET_SIZE_USD" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)
    WALLET=$(grep "^WALLET_ADDRESS" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2 | head -c 14)
    echo -e "  DRY_RUN=$DRY"
    echo -e "  BET_SIZE=\$$BET"
    echo -e "  WALLET=$WALLET..."
else
    echo -e "${RED}  NO .env FILE!${NC}"
    echo "  Create one at $DEPLOY_DIR/.env with:"
    echo "    POLYMARKET_PRIVATE_KEY=0x..."
    echo "    WALLET_ADDRESS=0x..."
    echo "    DRY_RUN=false"
    echo "    DEFAULT_BET_SIZE_USD=5.0"
    echo "    MAX_BET_SIZE_USD=25.0"
    exit 1
fi
echo -e "${GREEN}[✓] Config OK${NC}"

# 5. Create systemd service & start
echo -e "${CYAN}[5/5] Starting bot...${NC}"
VENV_PYTHON="$DEPLOY_DIR/venv/bin/python3"
[ ! -f "$VENV_PYTHON" ] && VENV_PYTHON="$DEPLOY_DIR/.venv/bin/python3"

cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Polymarket Real Trading Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$DEPLOY_DIR
EnvironmentFile=$DEPLOY_DIR/.env
ExecStart=$VENV_PYTHON main.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME 2>/dev/null
systemctl start $SERVICE_NAME
sleep 5

# Test
echo ""
RESULT=$(curl -s http://localhost:5002/api/state 2>/dev/null)
if echo "$RESULT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d:
        print(f'  Error: {d[\"error\"]}')
        sys.exit(1)
    print(f'  Mode:      {d.get(\"mode\", \"?\")}{\" (DRY RUN)\" if d.get(\"dry_run\") else \" (LIVE)\"}')
    print(f'  Balance:   \${d.get(\"usdc_balance\", 0):.2f}')
    print(f'  Portfolio: \${d.get(\"portfolio_value\", 0):.2f}')
    print(f'  Positions: {len(d.get(\"positions\", []))}')
    print(f'  Bet size:  \${d.get(\"default_bet\", 5):.2f}')
    print(f'  Running:   {d.get(\"running\", False)}')
except Exception as e:
    print(f'  Parse error: {e}')
    sys.exit(1)
" 2>/dev/null; then
    echo ""
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║         REAL BOT IS RUNNING                   ║"
    VPS_IP=$(hostname -I | awk '{print $1}')
    printf "║  Dashboard:  http://%-26s ║\n" "$VPS_IP:5002"
    echo "║  Logs:       journalctl -u $SERVICE_NAME -f   ║"
    echo "║  Stop:       systemctl stop $SERVICE_NAME     ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
else
    echo -e "${RED}  Dashboard not responding. Check:${NC}"
    echo "  journalctl -u $SERVICE_NAME -n 30 --no-pager"
fi
