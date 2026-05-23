@AGENTS.md

# Mobile App — Session Scope

This session works ONLY on the mobile Next.js app in this directory (`mobile/`).

**In scope:**
- Everything under `mobile/src/`
- `mobile/next.config.ts`, `mobile/package.json`, `mobile/.env.local`
- The five views: ApexView, TodayView, HoldingsView, ActivityView, ResultsView
- Components, lib/api.ts, lib/translate.ts

**Out of scope — do not touch:**
- `bot_dashboard.py` — the bot's operational dashboard (port 8080)
- Anything outside the `mobile/` directory
- Bot engine files, trading logic, Python code

The bot at `http://192.168.1.221:8080` is a read-only data source. We call its APIs, we never modify it.
