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
store "ANTHROPIC_API_KEY"  "$ANTHROPIC_API_KEY"
store "IBKR_ACTIVE_ACCOUNT" "$IBKR_ACTIVE_ACCOUNT"
store "IBKR_PAPER_ACCOUNT"  "$IBKR_PAPER_ACCOUNT"
store "IBKR_LIVE_1_ACCOUNT" "$IBKR_LIVE_1_ACCOUNT"
store "IBKR_LIVE_2_ACCOUNT" "$IBKR_LIVE_2_ACCOUNT"
echo ""
echo "Done. Secrets are now in iCloud Keychain and will sync to any Mac"
echo "signed into Apple ID: $(security find-identity 2>/dev/null | head -1 || echo 'your Apple ID')"
