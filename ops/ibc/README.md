# IBC — IBKR Gateway Headless Automation

**Directory:** `ops/ibc/`
**Purpose:** Configuration templates and installation guide for IBC (IBController),
the open-source tool that automates IB Gateway login for headless cloud operation.

> ⚠️  **SECURITY WARNING**
> This directory contains configuration TEMPLATES only.
> Real credentials must NEVER be committed to this repository.
> The actual `config.ini` file with real credentials lives on the VM at
> `/opt/decifer/ibc/config.ini` and is NEVER checked into git.

---

## What IBC Does

IBC (IBController) is an open-source utility that:
1. Starts IB Gateway in headless mode using Xvfb (a virtual X11 display)
2. Automatically enters your IBKR username and password at the login prompt
3. Handles the GUI dialogs that IB Gateway shows on startup
4. Manages Gateway session restarts (daily Gateway reset)
5. Keeps the Gateway process alive under supervision

Without IBC, IB Gateway requires a human to click through a login dialog — which is
not possible in a headless cloud environment.

**IBC GitHub:** https://github.com/IbcAlpha/IBC

---

## Installation on the VM

All commands below run on the DigitalOcean Droplet as root.

### Step 1: Download IBC

```bash
# Check for latest release at: https://github.com/IbcAlpha/IBC/releases
IBC_VERSION="3.19.0"   # Replace with latest stable version
cd /opt/decifer/ibc

wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBC-${IBC_VERSION}.zip" \
  -O ibc.zip

unzip -q ibc.zip -d .
rm ibc.zip
chmod +x /opt/decifer/ibc/scripts/*.sh 2>/dev/null || true
```

### Step 2: Download IB Gateway installer

```bash
# Download IB Gateway (stable channel, paper trading)
# Find current installer at: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
wget -q "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh" \
  -O /opt/decifer/ibc/ibgateway_installer.sh

chmod +x /opt/decifer/ibc/ibgateway_installer.sh
```

### Step 3: Install IB Gateway (headless)

```bash
# Run installer — headless mode (-q = silent, -dir = install location)
# This requires Java 17 (installed by setup_digitalocean_vm_base.sh)
/opt/decifer/ibc/ibgateway_installer.sh -q \
  -dir /opt/ibgateway \
  -DSilentInstall=true

# Set ownership
chown -R decifer:decifer /opt/ibgateway
```

### Step 4: Create IBC config.ini with real credentials

```bash
# Copy the template from the repo
cp /opt/decifer/current/ops/ibc/config.ini.template /opt/decifer/ibc/config.ini

# Edit with your actual IBKR credentials
# DO NOT use your live account credentials here — paper account only
vim /opt/decifer/ibc/config.ini

# Lock down permissions — this file contains your IBKR password
chmod 600 /opt/decifer/ibc/config.ini
chown root:root /opt/decifer/ibc/config.ini
```

**Fields you must fill in:**

| Field | Description |
|-------|-------------|
| `IbLoginId` | Your IBKR paper account username |
| `IbPassword` | Your IBKR paper account password |
| `TradingMode` | Must be `paper` for Phase 1 |
| `IbPort` | Must be `4002` (IB Gateway paper port) |

### Step 5: Test IBC manually before enabling systemd

```bash
# Start Xvfb manually first
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1

# Start IBC (this will open IB Gateway and auto-login)
/opt/decifer/ibc/scripts/ibcstart.sh \
  /opt/decifer/ibc/config.ini \
  --IbDir=/opt/ibgateway \
  --LogToConsole

# Watch the output — you should see "IB Gateway started" and "Logged in successfully"
# Check that Gateway is listening:
sleep 30
nc -z 127.0.0.1 4002 && echo "Gateway port 4002 is open" || echo "NOT open — check IBC logs"

# Stop the manual test before enabling systemd services
pkill -f ibgateway || true
pkill -f Xvfb || true
```

### Step 6: Enable systemd services

Once the manual test passes:

```bash
bash /opt/decifer/current/scripts/cloud/install_systemd_services.sh --enable
# Then, after Amit's approval, to start services:
bash /opt/decifer/current/scripts/cloud/install_systemd_services.sh --start --confirm-start
```

---

## 2FA (Two-Factor Authentication) Note

**Paper accounts:** IBKR allows 2FA suppression for paper accounts when using
IBC's automated login. Set `ExistingSessionDetectedAction=primary` and
`OverrideTwsApiPort=4002` in `config.ini`. The first login may still prompt
for 2FA — complete it once manually via VNC, after which IBC maintains the session.

**Live accounts:** 2FA cannot be reliably suppressed for live accounts via IBC.
This requires the IBKR SLS (Secure Login System) mobile device setup.
Do not attempt live trading until 2FA suppression is confirmed working.

---

## VNC Access (manual Gateway login / troubleshooting)

If IBC cannot suppress 2FA, install a VNC server to complete the first login manually:

```bash
apt-get install -y tigervnc-standalone-server
# Start VNC on display :1 (Xvfb already using :1 — use :2 for VNC)
vncserver :2 -geometry 1280x800 -depth 24
# Connect from your machine: ssh -L 5902:localhost:5902 user@<vm-ip>
# Then open VNC viewer to localhost:5902
```

---

## IBC Logs

IBC logs are written to `/opt/decifer/ibc/logs/`.
On systemd, also check: `journalctl -u decifer-ibgateway -f`

---

## Files in This Directory

| File | Purpose |
|------|---------|
| `README.md` | This file — installation guide |
| `config.ini.template` | IBC config template — fill in credentials on the VM, never commit |

**Files that exist on the VM only (never in git):**

| File | Location on VM |
|------|---------------|
| `config.ini` (real credentials) | `/opt/decifer/ibc/config.ini` |
| `ibc.zip` / unpacked IBC | `/opt/decifer/ibc/` |
| IB Gateway installation | `/opt/ibgateway/` |
| IBC logs | `/opt/decifer/ibc/logs/` |
