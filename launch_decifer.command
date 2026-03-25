#!/bin/bash
# <> Decifer — Launch Script
# Double-click this file to start Decifer

# Navigate to workspace
cd "$(dirname "$0")"

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "⚠️  .env file not found — copy .env and add your ANTHROPIC_API_KEY"
    exit 1
fi

echo ""
echo "  <> Decifer — Starting..."
echo "  Dashboard: http://localhost:8080"
echo ""

# Install / update dependencies (fast no-op if already up to date)
echo "  Checking dependencies..."
pip3 install -q -r requirements.txt

# Open dashboard in browser after 3 seconds
sleep 3 && open http://localhost:8080 &

python3 bot.py
