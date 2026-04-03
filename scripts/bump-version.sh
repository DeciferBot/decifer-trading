#!/usr/bin/env bash
# Usage: ./scripts/bump-version.sh 1.4.0 "IC Weighted Scoring"
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

# Update version.py
sed -i '' "s/__version__ = .*/__version__ = \"$VERSION\"/" "$VERSION_FILE"
sed -i '' "s/__codename__ = .*/__codename__ = \"$CODENAME\"/" "$VERSION_FILE"

echo "✓ version.py updated to v$VERSION ($CODENAME)"

# Commit + tag
cd "$REPO_ROOT"
git add version.py
git commit -m "chore(version): bump to v$VERSION — $CODENAME

Approved-by: Amit"
git tag -a "v$VERSION" -m "v$VERSION: $CODENAME"
git push && git push origin --tags

echo "✓ v$VERSION tagged and pushed to GitHub"
