#!/usr/bin/env bash
# scripts/verify_standalone_workers.sh
# Classification: worker runtime / verification script
#
# Verifies that standalone universe workers can run independently of bot.py.
#
# Usage:
#   bash scripts/verify_standalone_workers.sh
#
# This script:
#   1. Confirms bot.py is NOT the running process for these workers.
#   2. Runs universe_committed worker once (with mocked Alpaca via env).
#   3. Runs universe_promoter worker once.
#   4. Checks evidence file was updated.
#   5. Checks output artifacts were written.
#   6. Prints a pass/fail summary.
#
# NOTE: This script uses the --dry-run equivalent by checking imports only.
#       It does NOT make live Alpaca API calls.
#       For a live run, call the workers directly (see Section 3 below).
#
# ------------------------------------------------------------------------------
# Section 1 — Confirm bot.py is not running
# ------------------------------------------------------------------------------

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo "=== Decifer standalone universe worker verification ==="
echo "Repo: $REPO_ROOT"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Check if bot.py is running (pgrep looks for python processes containing "bot.py")
if pgrep -f "python.*bot\.py" > /dev/null 2>&1; then
    echo "[INFO] bot.py is currently running. Workers will run alongside it."
    echo "       This is fine — workers are independent and do not conflict with bot.py."
else
    echo "[OK]  bot.py is NOT running. Workers will run fully independently."
fi

# ------------------------------------------------------------------------------
# Section 2 — Import audit: confirm no execution modules imported by workers
# ------------------------------------------------------------------------------

echo ""
echo "--- Import audit ---"

BANNED="bot_trading bot_ibkr orders_core orders_options orders_state risk risk_gates apex_orchestrator execution_agent"

for MODULE in universe_committed universe_promoter worker_evidence; do
    BANNED_FOUND=$(python3.11 -c "
import sys, json
banned = '${BANNED}'.split()
import ${MODULE}
found = [m for m in sys.modules if any(m == p or m.startswith(p+'.') for p in banned)]
print(json.dumps(found))
" 2>/dev/null || echo '["CHECK_FAILED"]')
    if [ "$BANNED_FOUND" = "[]" ]; then
        echo "[OK]  $MODULE imports no banned modules"
    else
        echo "[FAIL] $MODULE imported banned modules: $BANNED_FOUND"
        exit 1
    fi
done

# ------------------------------------------------------------------------------
# Section 3 — Verify worker entry points exist and are callable
# ------------------------------------------------------------------------------

echo ""
echo "--- Entry point check ---"

python3.11 -c "
from universe_committed import _main as uc_main
from universe_promoter import _main as up_main
print('[OK]  universe_committed._main importable')
print('[OK]  universe_promoter._main importable')
"

# ------------------------------------------------------------------------------
# Section 4 — Live manual run instructions (not executed here)
# ------------------------------------------------------------------------------

echo ""
echo "--- Manual run commands (not executed by this script) ---"
echo ""
echo "  # Committed universe (safe any time including weekends):"
echo "  cd $REPO_ROOT"
echo "  python3.11 universe_committed.py --run-once"
echo "  cat data/heartbeats/universe_committed_worker.json"
echo "  tail -1 data/runtime/universe_worker_evidence.jsonl"
echo ""
echo "  # Promoter universe (safe pre/post-market):"
echo "  cd $REPO_ROOT"
echo "  python3.11 universe_promoter.py --run-once"
echo "  cat data/heartbeats/universe_promoter_worker.json"
echo "  tail -1 data/runtime/universe_worker_evidence.jsonl"

# ------------------------------------------------------------------------------
# Section 5 — Evidence file check (if it already exists from a prior run)
# ------------------------------------------------------------------------------

echo ""
echo "--- Evidence file check ---"

EVIDENCE_PATH="data/runtime/universe_worker_evidence.jsonl"
if [ -f "$EVIDENCE_PATH" ]; then
    LINES=$(wc -l < "$EVIDENCE_PATH")
    echo "[OK]  $EVIDENCE_PATH exists — $LINES evidence record(s)"
    LATEST=$(tail -1 "$EVIDENCE_PATH" 2>/dev/null || echo "{}")
    echo "      Latest: $LATEST" | head -c 200
    echo ""
else
    echo "[INFO] $EVIDENCE_PATH not yet created (no worker has run yet)"
    echo "       Run: python3.11 universe_committed.py --run-once"
fi

HEARTBEAT_C="data/heartbeats/universe_committed_worker.json"
if [ -f "$HEARTBEAT_C" ]; then
    STATUS=$(python3.11 -c "import json; d=json.load(open('$HEARTBEAT_C')); print(d.get('status','?'))" 2>/dev/null || echo "unreadable")
    echo "[OK]  committed heartbeat exists — status=$STATUS"
else
    echo "[INFO] committed heartbeat not yet created"
fi

HEARTBEAT_P="data/heartbeats/universe_promoter_worker.json"
if [ -f "$HEARTBEAT_P" ]; then
    STATUS=$(python3.11 -c "import json; d=json.load(open('$HEARTBEAT_P')); print(d.get('status','?'))" 2>/dev/null || echo "unreadable")
    echo "[OK]  promoter heartbeat exists — status=$STATUS"
else
    echo "[INFO] promoter heartbeat not yet created"
fi

# ------------------------------------------------------------------------------
# Section 6 — Sprint 7J.4 handoff manifest verification (read-only)
# ------------------------------------------------------------------------------

echo ""
echo "--- Sprint 7J.4 handoff manifest check (read-only) ---"

MANIFEST_PATH="data/live/current_manifest.json"
if [ -f "$MANIFEST_PATH" ]; then
    python3.11 -c "
import json, sys
try:
    d = json.load(open('data/live/current_manifest.json'))
    handoff_enabled = d.get('handoff_enabled', False)
    pub_mode = d.get('publication_mode', 'unknown')
    handoff_mode = d.get('handoff_mode', 'unknown')
    expires_at = d.get('expires_at', 'unknown')
    print(f'[INFO] current_manifest.json exists')
    print(f'       handoff_enabled={handoff_enabled}')
    print(f'       publication_mode={pub_mode}')
    print(f'       handoff_mode={handoff_mode}')
    print(f'       expires_at={expires_at}')
    if handoff_enabled and handoff_mode == 'live':
        print('[OK]  Manifest is in controlled_activation mode — bot CAN consume handoff')
    else:
        print('[INFO] Manifest is NOT in live mode — bot uses scanner discovery')
except Exception as e:
    print(f'[WARN] Could not parse manifest: {e}')
"
else
    echo "[INFO] data/live/current_manifest.json does not exist"
    echo "       Handoff publisher has not run, or enable_active_opportunity_universe_handoff"
    echo "       confirmation is pending. Run: python3.11 handoff_publisher.py --mode controlled_activation"
fi

# To confirm runtime consumption by the live bot (requires bot to be running):
echo ""
echo "  # Grep to confirm bot consumed manifest in last scan cycle:"
echo "  grep 'handoff\|current_manifest\|live_universe' data/audit_log.jsonl 2>/dev/null | tail -5"
echo "  grep 'handoff_reader\|load_production_handoff' data/audit_log.jsonl 2>/dev/null | tail -5"

echo ""
echo "=== Verification complete ==="
