#!/usr/bin/env bash
# Decifer Trading — Store secrets in iCloud Keychain
# Run once after updating .env to sync secrets to iCloud Keychain.
# Secrets will auto-sync to any Mac signed into the same Apple ID.
#
# Usage: ./scripts/store-secrets.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
KEYCHAIN_ACCOUNT="amit@decifer"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env not found at $ENV_FILE"
    exit 1
fi

# Load .env
source "$ENV_FILE"

store() {
    local key=$1
    local value=$2
    if [ -n "$value" ]; then
        security add-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$key" -w "$value" -U
        echo "  ✓ $key stored"
    else
        echo "  ⚠ $key is empty — skipped"
    fi
}

echo "Storing Decifer secrets in iCloud Keychain..."
store "ANTHROPIC_API_KEY"    "$ANTHROPIC_API_KEY"
store "ALPACA_API_KEY"       "$ALPACA_API_KEY"
store "ALPACA_SECRET_KEY"    "$ALPACA_SECRET_KEY"
store "ALPACA_BASE_URL"      "$ALPACA_BASE_URL"
store "FMP_API_KEY"          "$FMP_API_KEY"
store "ALPHA_VANTAGE_KEY"    "$ALPHA_VANTAGE_KEY"
store "FRED_API_KEY"         "$FRED_API_KEY"
store "IBKR_ACTIVE_ACCOUNT"  "$IBKR_ACTIVE_ACCOUNT"
store "IBKR_PAPER_ACCOUNT"   "$IBKR_PAPER_ACCOUNT"
echo ""
echo "Done. Secrets are now in iCloud Keychain and will sync to any Mac"
echo "signed into Apple ID: $(defaults read MobileMeAccounts Accounts 2>/dev/null | grep AccountID | head -1 | awk '{print $3}' | tr -d '\"' || echo 'your Apple ID')"
