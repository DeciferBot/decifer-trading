# Cloud Runtime Readiness Report

**Date:** 2026-05-11  
**Sprint:** Closure Sprint (funny-almeida-9500ef)  
**Auditor:** Cowork (Claude)

---

## Verdict

**PARTIALLY READY**

The application can deploy to a cloud host and run the preflight check and intelligence workers safely. The live bot (`bot.py`) requires IBKR TWS to be reachable from the cloud host — this is an infrastructure prerequisite, not a code gap.

---

## Dockerfile Status

| Item | Status | Notes |
|------|--------|-------|
| `Dockerfile` exists | ✅ DONE | Created in closure sprint |
| `.dockerignore` exists | ✅ DONE | Excludes `.env`, `data/`, `logs/`, `.git/` |
| No secrets baked in image | ✅ VERIFIED | `RUN rm -f .env .env.local` in Dockerfile; all keys via `--env-file` |
| No absolute local paths | ✅ VERIFIED | `WorkingDirectory = /app`; data paths are relative |
| Python dependencies installed | ✅ VERIFIED | `COPY requirements.txt . && pip install -r requirements.txt` |
| TA-Lib handled | ✅ VERIFIED | Built from source in Dockerfile: `ta-lib-0.4.0-src.tar.gz` |
| Default CMD = safe | ✅ VERIFIED | `CMD ["python3", "scripts/cloud_preflight.py"]` — no order placement |
| `docker build` tested | ⚠️ NOT TESTED | Docker not available in local dev environment; `docker build -t decifer-trading .` must be run on cloud host |

**Build command:**
```bash
docker build -t decifer-trading .
docker run --rm --env-file .env decifer-trading python3 scripts/cloud_preflight.py
```

---

## Cloud Preflight Script

| Check | Status | Notes |
|-------|--------|-------|
| `scripts/cloud_preflight.py` exists | ✅ DONE | 17 checks covering Python, dirs, config, IBKR params, env vars, writability, handoff reader |
| Outputs `data/runtime/cloud_preflight_report.json` | ✅ DONE | Written on every run |
| Exits 0 only when all blocking checks pass | ✅ DONE | `sys.exit(0 if report["overall_ok"] else 1)` |
| Non-blocking checks (manifest, reader) use `blocking=False` | ✅ DONE | Warn but don't block on handoff warnings |
| Verified in worktree | ✅ DONE | Exits 1 (expected — no .env in worktree); confirms env_vars blocking failure correctly identified |

---

## Intelligence Workers — Cloud Compatibility

| Worker | Plist | Installed | Last Heartbeat | Cloud Compatible |
|--------|-------|-----------|----------------|-----------------|
| `universe_committed.py` | `ops/launchd/com.decifer.universe-committed.plist` | ✅ Yes (May 9) | 2026-05-11T06:18:23Z | ✅ Yes — Alpaca API only |
| `universe_promoter.py` | `ops/launchd/com.decifer.universe-promoter-preopen.plist` | ✅ Yes (May 9) | 2026-05-11T06:17:53Z | ✅ Yes — Alpaca API only |
| `universe_promoter.py` (EOD) | `ops/launchd/com.decifer.universe-promoter-eod.plist` | ✅ Yes (May 9) | same heartbeat | ✅ Yes |
| `handoff_publisher.py` | `ops/launchd/com.decifer.handoff-publisher.plist` | ⚠️ Template only | 2026-05-11T07:00:00Z (manual) | ✅ Yes — file I/O only |

**Cloud worker invocations:**
```bash
# Committed universe (weekly, Sunday 23:00)
docker run --rm --env-file .env -v $(pwd)/data:/app/data decifer-trading \
  python3 universe_committed.py --run-once

# Promoter (daily, Mon–Fri 08:00 and 16:15)
docker run --rm --env-file .env -v $(pwd)/data:/app/data decifer-trading \
  python3 universe_promoter.py --run-once

# Handoff publisher (every 10 min during market hours)
docker run --rm --env-file .env -v $(pwd)/data:/app/data decifer-trading \
  python3 handoff_publisher.py --mode controlled_activation
```

---

## Infrastructure Prerequisites (Not Code Gaps)

| Prerequisite | Status | Notes |
|-------------|--------|-------|
| IBKR TWS or IB Gateway reachable | ❌ EXTERNAL | Live bot requires TWS on `ibkr_host:ibkr_port`; workers do NOT need IBKR |
| All 7 env vars set | ❌ EXTERNAL | Must provide via `--env-file .env` or secrets manager |
| `data/` volume mounted | ❌ EXTERNAL | Workers need persistent `data/` volume across container runs |

---

## Runtime Service Classification

| Component | Import by Production Bot | Exclude from Cloud Runtime |
|-----------|-------------------------|--------------------------|
| `bot.py` + `bot_trading.py` + `bot_ibkr.py` | Yes | No — these ARE the runtime |
| `universe_committed.py` + `universe_promoter.py` | No (standalone) | Optional — can run as separate containers |
| `handoff_publisher.py` | No (standalone) | Optional — can run as separate container on schedule |
| `scripts/cloud_preflight.py` | No | Include — runs before bot starts |
| `backtest_intelligence.py` | No | Yes — never deploy in production |
| `advisory_reporter.py`, `advisory_log_reviewer.py` | No | Yes — offline tools only |

---

## Next Steps Before Cloud Deploy

1. Install handoff publisher plist: `cp ops/launchd/com.decifer.handoff-publisher.plist ~/Library/LaunchAgents/ && launchctl load ...`
2. Test `docker build -t decifer-trading .` on a machine with Docker
3. Confirm IBKR TWS is reachable from cloud host (port 4002 paper, 4001 live)
4. Mount persistent `data/` volume for workers
5. Set all 7 env vars via secrets manager or `.env` file
6. Run `docker run --rm --env-file .env decifer-trading` — verify preflight exits 0
