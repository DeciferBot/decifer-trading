# Mobile Intelligence Surface — Private Deployment Guide

Sprint M2. Read before deploying `mobile.decifertrading.com`.

---

## Security model

```
┌─────────────────────────────────────────────────────────────────────┐
│  mobile.decifertrading.com                                          │
│                                                                     │
│  ① Cloudflare Access  →  identity gate (email OTP or SSO)          │
│  ② Cloudflare Tunnel  →  localhost:8081 (Nginx, mobile-only)        │
│  ③ Nginx filter       →  localhost:8080 (Decifer process)           │
│                                                                     │
│  Routes allowed:   /mobile   /api/mobile/*                          │
│  Routes blocked:   everything else → 404                            │
└─────────────────────────────────────────────────────────────────────┘

dashboard.decifertrading.com
  ① Cloudflare Tunnel  →  localhost:8080 (direct, unchanged)
```

Three independent layers:
1. **Cloudflare Access** — unauthorised users never reach the origin
2. **Nginx route filter** — authenticated users can only see mobile routes
3. **App-level enforcement** — `_is_remote_request()` blocks all POST mutations except `/api/mobile/ask`; `/api/mobile/ask` uses `read_only=True` (blocks pause/resume control intents)

---

## What is exposed through mobile.decifertrading.com

| Route | Method | Content |
|-------|--------|---------|
| `/mobile` | GET | Mobile HTML shell (static, no secrets) |
| `/api/mobile/now` | GET | Regime mood, market session, positions count, portfolio value, daily P&L |
| `/api/mobile/why` | GET | Macro drivers, themes, market read (sanitised, no raw config) |
| `/api/mobile/alpha` | GET | Candidate watch list, Apex last cycle (no entry prices, no sizes) |
| `/api/mobile/portfolio` | GET | Open positions: symbol, direction, P&L %, thesis (truncated at 280 chars) |
| `/api/mobile/ask` | POST | LLM Q&A using portfolio context; read-only; no TTS; no state mutation |

## What is NOT exposed

- `/api/state` — full bot state including settings, all trades, signal scores
- `/api/kill` — flatten all positions
- `/api/close` — close individual position
- `/api/scan` — force scan trigger
- `/api/settings` — live config mutation
- `/api/restart` — bot restart
- `/api/pause` — pause/resume trading
- `/` — dashboard HTML
- All other dashboard and admin routes

---

## Step 1 — Install Nginx on the droplet

```bash
sudo apt update && sudo apt install -y nginx
```

---

## Step 2 — Add the rate-limit zone to nginx.conf

Edit `/etc/nginx/nginx.conf`. Inside the `http { }` block add:

```nginx
limit_req_zone $binary_remote_addr zone=mobile_ask:1m rate=6r/m;
```

Then uncomment the `limit_req` line in `nginx-mobile.conf`.

---

## Step 3 — Install the Nginx server block

```bash
sudo cp deployment/nginx-mobile.conf /etc/nginx/sites-available/mobile-decifer
sudo ln -s /etc/nginx/sites-available/mobile-decifer /etc/nginx/sites-enabled/
sudo nginx -t          # must say "syntax is ok"
sudo systemctl reload nginx
```

Verify Nginx listens on port 8081 (local only):
```bash
sudo ss -tlnp | grep 8081
```

---

## Step 4 — Configure Cloudflare Tunnel

The tunnel config file is typically at `~/.cloudflared/config.yml` or
`/etc/cloudflared/config.yml`.

Add a new ingress rule for the mobile subdomain **before** the catch-all:

```yaml
tunnel: <your-tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json

ingress:
  # Existing dashboard route — do not change
  - hostname: dashboard.decifertrading.com
    service: http://localhost:8080

  # New mobile route — routes through Nginx filter on 8081
  - hostname: mobile.decifertrading.com
    service: http://localhost:8081

  # Catch-all
  - service: http_status:404
```

Restart the tunnel daemon:
```bash
sudo systemctl restart cloudflared
```

---

## Step 5 — Create Cloudflare Access policy

In the Cloudflare dashboard:

1. Go to **Zero Trust → Access → Applications → Add an application**
2. Select **Self-hosted**
3. **Application name:** Decifer Mobile
4. **Application domain:** `mobile.decifertrading.com`
5. **Session duration:** 24 hours (adjust to taste)
6. Under **Policies → Add a policy:**
   - Policy name: Owner access
   - Action: Allow
   - Include: Emails → `chopraa@gmail.com`
   - (Add any other permitted email addresses)
7. Save and deploy

After this, unauthenticated requests to `mobile.decifertrading.com` receive a
Cloudflare login page before any request reaches the origin.

---

## Step 6 — DNS record

In the Cloudflare dashboard under your domain DNS settings:

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| CNAME | mobile | `<tunnel-id>.cfargotunnel.com` | Proxied (orange cloud) |

The tunnel ID is in your `config.yml` credentials file.

---

## Step 7 — Smoke test

After deploying, run the following checks in order:

```bash
# 1. Nginx filter works locally (no auth needed on localhost)
curl -s http://127.0.0.1:8081/mobile | grep -c "<html"          # expect 1
curl -s http://127.0.0.1:8081/api/mobile/now | python3 -m json.tool   # expect JSON
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/       # expect 302 or 404
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/api/state   # must be 404
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/api/kill    # must be 404
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/api/scan    # must be 404
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/api/settings # must be 404

# 2. Dashboard still works (port 8080 unchanged)
curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/       # expect 200
curl -s http://127.0.0.1:8080/api/state | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok')"

# 3. Mobile Ask read-only — control intents return text, never mutate
curl -s -X POST http://127.0.0.1:8081/api/mobile/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "pause scanning"}' | python3 -m json.tool
# Expected: ok=true, answer contains "not available on mobile"
# NOT expected: answer="Scanning paused."

# 4. Rate limit — 11th request in rapid succession should get 429
for i in $(seq 1 5); do
  curl -s -X POST http://127.0.0.1:8081/api/mobile/ask \
    -H "Content-Type: application/json" \
    -d '{"question":"what is in my portfolio?"}' -o /dev/null -w "%{http_code}\n"
  sleep 11
done
# All should be 200 (spaced 11s apart, above 10s cooldown)
```

---

## Private deployment checklist

Before approving private deployment at mobile.decifertrading.com:

### Infrastructure
- [ ] Nginx installed and running on the droplet
- [ ] `nginx -t` passes with the mobile server block loaded
- [ ] Nginx listens on `127.0.0.1:8081` only (not 0.0.0.0)
- [ ] Cloudflare Tunnel config updated with `mobile.decifertrading.com → localhost:8081`
- [ ] Cloudflare Tunnel daemon restarted and healthy (`systemctl status cloudflared`)
- [ ] DNS CNAME record for `mobile` pointing to tunnel ID

### Access protection
- [ ] Cloudflare Access policy created for `mobile.decifertrading.com`
- [ ] Access policy allows only `chopraa@gmail.com` (and intended users)
- [ ] Unauthenticated access to `https://mobile.decifertrading.com` shows Cloudflare login — NOT the mobile shell

### Route isolation
- [ ] `https://mobile.decifertrading.com/mobile` loads after auth — shows mobile shell
- [ ] `https://mobile.decifertrading.com/api/mobile/now` returns JSON after auth
- [ ] `https://mobile.decifertrading.com/api/mobile/portfolio` returns JSON after auth
- [ ] `https://mobile.decifertrading.com/api/state` returns 404
- [ ] `https://mobile.decifertrading.com/` redirects to `/mobile` (or returns 404)
- [ ] `https://mobile.decifertrading.com/api/scan` returns 404
- [ ] `https://mobile.decifertrading.com/api/settings` returns 404
- [ ] `https://mobile.decifertrading.com/api/kill` returns 404

### Mobile Ask safety
- [ ] POST to `/api/mobile/ask` with question "pause scanning" returns answer containing "not available on mobile" — does NOT pause the bot
- [ ] POST to `/api/mobile/ask` returns text answer with no TTS side effect
- [ ] Rapid requests (< 10 s apart) return 429 cooldown error
- [ ] dashboard.decifertrading.com still loads and functions normally

### Compile check
- [ ] `python3 -m py_compile mobile_api.py bot_dashboard.py voice_agent.py` exits 0

---

## Public deployment — NOT approved

Public deployment (removing Cloudflare Access) requires:
- Authentication mechanism built into the mobile shell itself (token or session cookie)
- Rate limiting proven sufficient to prevent portfolio data scraping
- Legal review of what financial data can be publicly displayed

Do not remove Cloudflare Access protection without completing the above.

---

## Rollback

If the mobile subdomain causes issues:

```bash
# Remove Nginx site and reload
sudo rm /etc/nginx/sites-enabled/mobile-decifer
sudo systemctl reload nginx

# Remove tunnel ingress rule for mobile.decifertrading.com from config.yml
# Restart tunnel
sudo systemctl restart cloudflared

# Remove Cloudflare Access policy (optional)
```

The Decifer process on port 8080 and dashboard.decifertrading.com are unaffected.
