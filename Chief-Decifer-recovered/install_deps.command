#!/bin/bash
# ── Chief Decifer — Install Missing Dependencies ────────────────────────────
# Double-click this file in Finder to install all required Python packages.

cd "$(dirname "$0")"

echo ""
echo "  ◆ Chief Decifer — Dependency Installer"
echo "  ──────────────────────────────────────"
echo ""

# Find Python
PYTHON=""
for candidate in python3 python /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  ✗  Python not found. Please install Python 3 and try again."
    exit 1
fi

PYVER=$("$PYTHON" --version 2>&1)
echo "  Using: $PYTHON ($PYVER)"
echo ""

# Install only dash-draggable (everything else is already installed)
echo "  Installing dash-draggable ..."
"$PYTHON" -m pip install "dash-draggable==0.1.2"
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
    echo "  ✓  dash-draggable installed successfully!"
    echo ""
    echo "  Draggable tiles are now active."
    echo "  Launch Chief Decifer with Chief.command"
else
    echo "  ✗  Install failed (exit code $STATUS)"
    echo "  Try manually:  pip3 install dash-draggable==0.1.2"
fi

echo ""
echo "  Press any key to close..."
read -n 1 -s
