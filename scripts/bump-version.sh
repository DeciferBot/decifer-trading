#!/usr/bin/env bash
# Force a specific version and codename.
# Only needed when you want to override the automatic bump or change the codename.
# Normal versioning is fully automatic via .githooks/commit-msg:
#   feat  → MINOR   fix/refactor/chore/etc → PATCH   feat!/BREAKING CHANGE → MAJOR
#
# Usage: ./scripts/bump-version.sh 4.0.0 "New Codename"
set -e

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: ./scripts/bump-version.sh <VERSION> <CODENAME>"
    echo "  e.g. ./scripts/bump-version.sh 1.4.0 \"IC Weighted Scoring\""
    exit 1
fi

VERSION=$1
CODENAME=$2
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/version.py"
CHANGELOG="$REPO_ROOT/docs/CHANGELOG.md"

# ── CHANGELOG gate ────────────────────────────────────────────────────────────
# Check if CHANGELOG.md has been edited since last commit (staged or unstaged)
cd "$REPO_ROOT"
if git diff --quiet HEAD -- "$CHANGELOG" && git diff --cached --quiet -- "$CHANGELOG"; then
    echo ""
    echo "  CHANGELOG not updated. Open it now? [y/N]"
    read -r OPEN_IT
    if [ "$OPEN_IT" = "y" ] || [ "$OPEN_IT" = "Y" ]; then
        "${EDITOR:-nano}" "$CHANGELOG"
    else
        echo "Aborted. Update docs/CHANGELOG.md before releasing."
        exit 1
    fi
fi

# ── Update version.py ─────────────────────────────────────────────────────────
sed -i '' "s/__version__ = .*/__version__ = \"$VERSION\"/" "$VERSION_FILE"
sed -i '' "s/__codename__ = .*/__codename__ = \"$CODENAME\"/" "$VERSION_FILE"

echo "✓ version.py updated to v$VERSION ($CODENAME)"

# ── Commit + tag ──────────────────────────────────────────────────────────────
git add version.py "$CHANGELOG"
git commit -m "chore(version): bump to v$VERSION — $CODENAME

Approved-by: Amit"
git tag -a "v$VERSION" -m "v$VERSION: $CODENAME"
git push && git push origin --tags

echo "✓ v$VERSION tagged and pushed to GitHub"
