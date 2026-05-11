# IBKR Gateway — Future Cloud Design

**Status:** Design document only. No implementation performed.
**Branch:** `cloud/cloud-readiness-preparation`
**Classification:** Pre-migration architecture reference.

---

## Purpose

Interactive Brokers Gateway is the single hardest dependency to move to the cloud.
It requires a persistent GUI session (username/password), cannot be reached via a
public API, and uses a persistent TCP connection that breaks on restart.

This document defines the recommended future architecture, open questions,
and constraints that must be resolved before any actual cloud migration.

---

## 1. Current State (Local Development)

```
[IBKR TWS / Gateway]  ←→  [bot.py]
  localhost:7496              ↑
  (macOS desktop)        connects at startup
                         reconnects on disconnect
```

Connection parameters (as of Decifer 4.0.1):
- `ibkr_host`: read from `IBKR_HOST` env var, default `127.0.0.1`
- `ibkr_port`: read from `IBKR_PORT` env var, default `7496`
- `ibkr_client_id`: `10` (hardcoded in `config.py`)
- Reconnect: max 10 attempts, exponential backoff to 60s

Connection modules: `bot_ibkr.py`, `bot_state.py`, `ibkr_streaming.py`

---

## 2. The Core Challenge

IBKR Gateway is not a standard cloud service:

| Property | IBKR Gateway reality |
|----------|---------------------|
| Authentication | Interactive GUI login (username + password + 2FA) |
| API type | Local TCP socket (no public HTTPS endpoint) |
| Session duration | Restarts required periodically (daily or on config change) |
| Multi-region | Not applicable — one connection per account |
| SLA | No cloud SLA; subject to IBKR maintenance windows |
| Containerisation | Possible with IBC/IBController but requires headless X11 or VNC |
| Port | Paper Gateway: 4002; Paper TWS: 7497; Live Gateway: 4001; Live TWS: 7496 |

---

## 3. Recommended Future Architecture

### Option A: Same VM (Phase 1 Recommended)

```
Cloud VM
├── IBKR Gateway (process or container, port 4002 / 7497)
│     managed by: IBC / IBController with Xvfb (headless X11)
│     or: IB Gateway in a VNC container
└── Decifer bot container
      IBKR_HOST=127.0.0.1 (or container IP)
      IBKR_PORT=4002 (Gateway paper)
```

**Rationale:**
- No network routing between bot and Gateway — same host, same security group
- Simplest failure mode: if the VM restarts, both restart together
- Lowest latency for order submission

**Drawbacks:**
- IBKR Gateway failures affect the bot VM
- Gateway requires periodic manual re-authentication (session expiry)
- Headless X11 (Xvfb) adds complexity

### Option B: Separate VM (Phase 2 / Future)

```
Cloud VPC
├── VM-A: IBKR Gateway VM
│     Port 4002 exposed on private network only (never public internet)
│     IBC/IBController manages Gateway session
└── VM-B: Decifer Bot VM
      IBKR_HOST=<VM-A private IP>
      IBKR_PORT=4002
      Network: private VPC subnet only
```

**Rationale:**
- Bot can restart independently of Gateway
- Gateway VM can be dedicated hardware (lower restart frequency)

**Drawbacks:**
- More infrastructure to manage
- Gateway TCP port must be firewalled to bot VM only (never public)
- Additional latency (< 1ms on same VPC; negligible for paper trading)

### Option C: IBC + Docker (Advanced)

```
docker-compose.yml:
  ibkr-gateway:
    image: ghcr.io/extrange/ibkr-gateway:latest   # or similar community image
    environment:
      - TWS_USERID=...
      - TWS_PASSWORD=...
      - TRADING_MODE=paper
    ports:
      - "4002:4002"   # expose on private network only

  live-bot:
    depends_on: [ibkr-gateway]
    environment:
      - IBKR_HOST=ibkr-gateway
      - IBKR_PORT=4002
```

**Note:** Community Docker images for IBKR Gateway exist (e.g. `ghcr.io/extrange/ibkr-gateway`)
but are not IBKR-official and may break on Gateway version updates.
Evaluate carefully before adopting. IBKR credentials in environment variables
require secrets manager integration — never in docker-compose.yml directly.

---

## 4. Host and Port Environment Variable Expectations

As of Decifer 4.0.1, `config.py` reads:

```python
"ibkr_host": os.environ.get("IBKR_HOST", "127.0.0.1"),
"ibkr_port": int(os.environ.get("IBKR_PORT", "7496")),
```

This means the host and port are fully configurable at deployment time.
No code changes are needed to point the bot at a different Gateway address.

Standard port reference:

| Mode | Application | Port |
|------|------------|------|
| Paper | IB Gateway | 4002 |
| Paper | TWS | 7497 |
| Live | IB Gateway | 4001 |
| Live | TWS | 7496 |
| Paper (legacy default) | TWS/Gateway | 7496 |

---

## 5. Security Considerations

| Risk | Mitigation |
|------|-----------|
| Gateway port exposed on public internet | Firewall rule: only allow bot's private IP; never `0.0.0.0/0` |
| IBKR credentials in environment | Use cloud secrets manager (AWS SSM, GCP Secret Manager); rotate on schedule |
| TWS_USERID / TWS_PASSWORD in IBC config | If using IBC Docker: inject via secrets, never baked into image |
| 2FA / authenticator requirement | IBC can suppress 2FA for paper accounts; live accounts require careful setup |
| Session expiry | IBC can auto-restart Gateway on daily reset; must be tested for paper first |
| Client ID collision | `ibkr_client_id=10` hardcoded; only one bot process must connect per client ID |

---

## 6. Restart and Reconnect Expectations

**Gateway restart (expected to be infrequent):**

1. IBKR Gateway disconnects all clients.
2. `bot_ibkr.py` reconnect loop fires — attempts reconnect up to 10 times with
   exponential backoff (max 60s between attempts).
3. If Gateway is not back within max attempts, bot halts with a logged error.
4. On Gateway comeback, bot restarts automatically (via supervisor/systemd `restart: unless-stopped`).
5. On reconnect, bot reconciles positions with IBKR to detect any fills that occurred
   during the disconnection window.

**Bot restart (independent of Gateway):**

1. Bot starts, attempts IBKR connection.
2. If Gateway is not running, bot logs connection failure and exits (or retries per config).
3. Supervisor/systemd must NOT restart the bot in a tight loop if Gateway is down.
   Use `restart: on-failure:3` with delay to avoid restart storms.

---

## 7. What Happens if IBKR Gateway is Unavailable

| Scenario | Bot behaviour |
|----------|--------------|
| Gateway not started | Bot fails at startup, exits non-zero |
| Gateway unreachable (network) | Bot attempts 10 reconnections, then halts |
| Gateway session expired | Same as unreachable; IBC must restart it |
| Gateway restarting (brief) | Reconnect loop catches it; bot resumes within ~60s |
| Gateway down for > 10 min | Bot halts; supervisor waits for manual Gateway restart |

**Critical:** open positions are NOT automatically closed if the bot disconnects
from Gateway. IBKR continues to manage any GTC orders. Bracket orders remain
active on the IBKR server side. This is safe for paper trading.

---

## 8. What Must Never Be Hardcoded

| Item | Reason |
|------|--------|
| IBKR username / password | Credentials rotation, security |
| IBKR host IP | Changes per environment |
| IBKR port | Changes per mode (paper/live) |
| Account ID | Paper vs live account separation |
| Client ID | Must be configurable if running multiple bot instances |
| 2FA secrets | Must be in secrets manager |

Currently hardcoded and **must be made configurable before Phase 2**:
- `ibkr_client_id: 10` in `config.py` (always 10; fine for single-bot Phase 1)

---

## 9. Open Questions Before Actual Migration

| # | Question | Blocking for |
|---|----------|-------------|
| 1 | Which cloud provider? (AWS, GCP, Hetzner, DigitalOcean) | VM selection, networking, secrets manager |
| 2 | IBC vs community Docker image vs manual headless X11? | Gateway automation approach |
| 3 | Does paper 2FA suppression work reliably with IBC? | Unattended restart capability |
| 4 | What is the daily Gateway session reset time in cloud timezone? | IBC restart window scheduling |
| 5 | How is Gateway authenticated on first cloud VM deploy? | Manual step vs automation |
| 6 | Same VM or separate VM for Gateway and bot? | Phase 1 architecture decision |
| 7 | What happens to open positions if VM is rebooted mid-trading-day? | Risk protocol definition |
| 8 | How are IBKR credentials stored in cloud secrets manager? | Secrets policy |
| 9 | Is `ibkr_client_id=10` safe if we ever run a second bot process? | Multi-process safety |
| 10 | How does monitoring detect a Gateway restart that bot missed? | Alerting design |
