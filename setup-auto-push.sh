#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   <> DECIFER — One-time setup for auto git push              ║
# ║   Run this ONCE: ./setup-auto-push.sh                        ║
# ║   It installs a background job that pushes every 2 minutes   ║
# ╚══════════════════════════════════════════════════════════════╝

REPO_DIR="$HOME/Documents/claude/projects/decifer-trading"
PLIST_NAME="com.decifer.auto-push"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/$PLIST_NAME.plist"
SCRIPT="$REPO_DIR/auto-push.sh"

echo "Setting up Decifer auto-push..."

# Make the push script executable
chmod +x "$SCRIPT"

# Create LaunchAgents dir if missing
mkdir -p "$PLIST_DIR"

# Unload old version if it exists
launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null

# Write the launchd plist
cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$SCRIPT</string>
    </array>
    <key>StartInterval</key>
    <integer>120</integer>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>StandardOutPath</key>
    <string>$REPO_DIR/logs/auto-push-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO_DIR/logs/auto-push-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF

# Load it
launchctl bootstrap "gui/$(id -u)" "$PLIST_FILE"

echo ""
echo "Done! Auto-push is now running in the background."
echo "  - Checks every 2 minutes for unpushed commits"
echo "  - Pushes automatically to GitHub"
echo "  - Log: $REPO_DIR/logs/auto-push.log"
echo ""
echo "To stop it later:  launchctl bootout gui/$(id -u)/$PLIST_NAME"
echo "To check status:   launchctl print gui/$(id -u)/$PLIST_NAME"
