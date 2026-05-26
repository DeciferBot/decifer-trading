# Decifer Mobile — Production Smoke Checklist

Use after every mobile deploy to https://mobile.decifertrading.com/customer.

---

## Pre-check (local, before opening browser)

- [ ] `npm test` — all tests pass
- [ ] `npx tsc --noEmit` — no TypeScript errors
- [ ] `npm run lint` — zero errors in source files (`.vercel/**` is excluded)
- [ ] `npm run build` — build succeeds locally

---

## App load

- [ ] https://mobile.decifertrading.com/customer loads within 3 seconds
- [ ] Header shows: DECIFER · MARKET INTELLIGENCE · version (not "vdev")
- [ ] No crash / blank screen
- [ ] No hydration errors in browser console

---

## Tab checks (current tab set: Today / Forces / Ask / Themes / Names)

### Today
- [ ] Market story headline renders
- [ ] Regime badge present
- [ ] "Since you were away" section loads (or is correctly absent)
- [ ] "Ask why" / "See forces" CTAs present
- [ ] Disclaimer: "Market intelligence only. Not financial advice. No trade execution."

### Forces
- [ ] Active forces list renders with at least 1 force
- [ ] Each force has a connection path and "Ask Decifer" CTA
- [ ] Dormant forces section present (collapsed)

### Ask
- [ ] Suggested questions render (static or contextual)
- [ ] Disclaimer: "Not financial advice. No trade recommendations."

### Themes
- [ ] Theme map renders with at least 1 active theme
- [ ] Active / Building Momentum / Weakening / Not Signalling sections visible

### Names
- [ ] "N evidence-verified names · M market stories" heading renders
- [ ] At least 1 story group with name cards
- [ ] Open 2 name detail sheets (tap any card):
  - [ ] "Why it matters now" — uses company name, not raw theme ID
  - [ ] Risk note (if present) — prefixed "For [Company]:"
  - [ ] No raw snake_case_id theme strings visible
  - [ ] No broker / order / execution / P&L / position-sizing language

---

## Safety scan (open browser devtools)

- [ ] Console: no unhandled errors
- [ ] Network: `/api/market-tape`, `/api/name-prices`, `/api/name-fundamentals` return HTTP 200 (data may be null after hours — that is expected)
- [ ] No `undefined` rendered visibly in the page

---

## Notes

- **Prices null after 4 PM ET**: FMP batch-quote returns null after market close. "Live price confirmation not available yet." is correct graceful fallback — not a bug.
- **Fundamentals null**: FMP profile/key-metrics may return `available: false` after hours — also expected. Sheet shows "Fundamentals context is not available" graceful text.
- **Version must not show "vdev"**: If it does, `mobile/version.json` is missing or stale — update it to match `version.py` and redeploy.
