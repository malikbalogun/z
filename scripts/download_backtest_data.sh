#!/usr/bin/env bash
#
# Download a hash-pinned snapshot of the Polymarket trade dataset for backtest.
#
# By design this script does NOT bake in a default URL or sha256 — those must
# come from environment variables. We deliberately avoid pinning a public mirror
# in the repo because:
#   1) The dataset originates from a third-party project (`warproxxx/poly_data`,
#      MIT-licensed) and we do not want to silently re-distribute it without an
#      explicit user opt-in.
#   2) Pinning to "whatever URL was current when the PR was written" gives
#      future readers a false sense of security; the user (or CI) should pass
#      the values they have audited.
#
# Usage:
#   PM_BACKTEST_DATA_URL="https://example.com/polymarket-trades-2026-03.csv.gz" \
#   PM_BACKTEST_DATA_SHA256="abc123...deadbeef" \
#   bash scripts/download_backtest_data.sh
#
# Or override the destination:
#   PM_BACKTEST_DATA_DEST="data/backtest/trades.csv.gz" bash scripts/download_backtest_data.sh
#
# After download succeeds, the file is sha256-verified and any mismatch fails
# the script (the partial download is removed).

set -euo pipefail

URL="${PM_BACKTEST_DATA_URL:-}"
SHA="${PM_BACKTEST_DATA_SHA256:-}"
DEST="${PM_BACKTEST_DATA_DEST:-data/backtest/trades.csv.gz}"

if [[ -z "$URL" || -z "$SHA" ]]; then
  cat <<'EOF' >&2
ERROR: PM_BACKTEST_DATA_URL and PM_BACKTEST_DATA_SHA256 must both be set.

Example:
  PM_BACKTEST_DATA_URL="https://example.com/snapshot.csv.gz" \
  PM_BACKTEST_DATA_SHA256="<sha256 hex>" \
  bash scripts/download_backtest_data.sh

Why no default URL?
  The dataset is third-party (poly_data, MIT-licensed). We refuse to silently
  fetch from a hard-coded mirror so users always see and approve the source.

For tests, you can skip this step entirely — the harness ships a small
synthetic fixture at tests/fixtures/backtest_mini.csv.
EOF
  exit 2
fi

mkdir -p "$(dirname "$DEST")"

echo "Downloading: $URL"
echo "  -> $DEST"

# Prefer curl, fall back to wget — both come pre-installed on the dev VM.
if command -v curl >/dev/null 2>&1; then
  curl -fsSL --retry 3 --retry-delay 2 -o "$DEST.partial" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget --tries=3 --waitretry=2 -qO "$DEST.partial" "$URL"
else
  echo "ERROR: neither curl nor wget is installed" >&2
  exit 3
fi

# Hash check — if it fails, never leave a half-validated file lying around.
echo "Verifying sha256..."
ACTUAL="$(sha256sum "$DEST.partial" | awk '{print $1}')"
EXPECTED="$(echo "$SHA" | tr 'A-Z' 'a-z' | sed -e 's/^sha256://' -e 's/[[:space:]]//g')"

if [[ "$ACTUAL" != "$EXPECTED" ]]; then
  echo "ERROR: sha256 mismatch" >&2
  echo "  expected: $EXPECTED" >&2
  echo "  actual:   $ACTUAL" >&2
  rm -f "$DEST.partial"
  exit 4
fi

mv "$DEST.partial" "$DEST"
SIZE_BYTES="$(stat -c %s "$DEST" 2>/dev/null || stat -f %z "$DEST")"
echo "OK: $DEST ($SIZE_BYTES bytes, sha256 verified)"
