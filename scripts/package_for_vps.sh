#!/usr/bin/env bash
# Build a zip safe to upload to a VPS (no .venv, no secrets, no local DB).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT="${1:-polymarket_real_bot-$(date +%Y%m%d-%H%M).zip}"
rm -f "$OUT"

zip -r "$OUT" . \
  -x "*.git/*" \
  -x "*/.venv/*" \
  -x "*/venv/*" \
  -x "*/__pycache__/*" \
  -x "*.pyc" \
  -x "*/__MACOSX/*" \
  -x "*.DS_Store" \
  -x "./data/*.db" \
  -x "./data/uploads/*" \
  -x "./config.json" \
  -x "./*.log" \
  -x "./polymarket_real_bot-*.zip"

echo "Created $(pwd)/$OUT"
ls -lh "$OUT"
