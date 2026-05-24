# DECIFER Trading — Homepage Deployment Boundary

**Version 1.0 — May 2026**

This document defines the hard boundary between the public homepage and all private DECIFER Trading infrastructure. It must be read before any change to `homepage/` that touches routing, APIs, or data.

---

## Surface map

| Surface | URL | Access | Codebase location |
|---------|-----|--------|-------------------|
| Public homepage | `decifertrading.com` | Public | `homepage/` (Next.js) |
| Mobile intelligence | `mobile.decifertrading.com` | Private — auth required | `mobile/` (Next.js, Cloudflare Tunnel) |
| Operator dashboard | `dashboard.decifertrading.com` | Private — auth required | `bot_dashboard.py` (Dash) |

---

## What the homepage is

- A public-facing brand and product page for DECIFER Trading
- Static in production (no runtime database, no broker connection, no live data)
- One dynamic API route: `POST /api/access` — receives early-access form submissions and forwards them by email via Resend. Collects: name, email, investor type, interest, optional message. Nothing else.

---

## What the homepage is not

The homepage does not and must never:

- Import from `bot_trading.py`, `bot_dashboard.py`, `market_intelligence.py`, or any other bot runtime module
- Call the IBKR TWS API or any broker endpoint
- Import `yfinance`, `alpaca-py`, `anthropic`, `ib_insync`, or any trading runtime dependency
- Read from `data/live/`, `data/trades.json`, `data/pm_engine/`, `data/signals_log.jsonl`, or any other runtime data file
- Expose positions, P&L, portfolio state, or execution logs
- Expose any bot API endpoint (`/api/scan`, `/api/apex`, `/api/pm`, etc.)
- Collect: financial account details, Emirates ID, passport data, bank details, or payment information
- Claim to be licensed, regulated, or approved in any jurisdiction
- Provide investment advice, trading recommendations, or guaranteed outcomes

---

## Private surface protection

### Mobile (`mobile.decifertrading.com`)

- Runs behind Cloudflare Tunnel — no direct port exposure
- Requires authentication before any content is served
- The homepage Sign In CTA links to `https://mobile.decifertrading.com` only — it does not embed mobile content, proxy mobile APIs, or share authentication state
- Mobile routing and security must not be changed via homepage work

### Dashboard

- `bot_dashboard.py` runs on an internal port, not publicly routed
- No homepage route points to the dashboard
- Dashboard state is never read or proxied by the homepage

---

## Access form — data handling

The `POST /api/access` route:

1. Validates name, email, investor type, interest (all required)
2. Checks a honeypot field — bot submissions are silently dropped
3. If `RESEND_API_KEY` is set in Vercel environment variables, emails the submission to the configured address
4. If `RESEND_API_KEY` is not set, logs the submission to Vercel function logs (visible in Vercel dashboard)
5. Returns `{ ok: true }` on success

No submission data is written to disk, no database is used, no user account is created.

---

## Isolation proof

Grep the following — all must return no results from `homepage/src/`:

```bash
# No bot runtime imports
grep -r "bot_trading\|bot_dashboard\|market_intelligence\|apex_call\|sentinel" homepage/src/

# No broker imports
grep -r "ib_insync\|ibkr\|alpaca\|yfinance" homepage/src/

# No trading data file reads
grep -r "data/live\|data/trades\|data/signals\|data/pm_engine" homepage/src/

# No anthropic SDK
grep -r "anthropic" homepage/src/
```

---

## Legal note (UAE and other jurisdictions)

DECIFER Trading is not licensed or approved as a financial services provider in the UAE or any other jurisdiction. The homepage must not make any claim to the contrary.

Required compliance copy (as appears in footer):

> DECIFER Trading provides market intelligence and decision-support context. It does not provide investment advice, trading recommendations, portfolio management, brokerage, order services, or guaranteed outcomes. All content is for informational purposes only. Past performance in research environments does not predict future results. Legal and regulatory review is required before any commercial launch in any jurisdiction.

Prohibited terms in public copy: guaranteed returns, beat the market, financial advice, investment adviser, licensed, regulated, approved, SCA, DFSA, VARA, CBUAE, ADGM, FSRA, DIFC, signals that make you rich.

---

## Change control

Any change to `homepage/` that would:
- Add a new external API call
- Add a new npm dependency with network/broker capability
- Change the Sign In destination
- Change what the access form collects
- Add a new route

...requires explicit review against this document before merging.

---

*Maintained by: Amit Chopra. Questions require explicit approval before any exception.*
