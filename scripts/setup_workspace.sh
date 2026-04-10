#!/usr/bin/env bash
# One-shot dev / VPS prep: venv, deps, data dir, config.json from example.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. On Ubuntu: sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

mkdir -p data
if [[ ! -f config.json ]] && [[ -f config.json.example ]]; then
  cp config.json.example config.json
  echo "Created config.json — set session_secret and initial_admin_password before production."
fi

echo "Workspace ready. Next:"
echo "  source .venv/bin/activate && python main.py"
echo "Or: make test"
