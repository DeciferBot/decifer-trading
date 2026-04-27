# Decifer 3.0 — Cutover Handoff

**Date:** 2026-04-24
**Commit:** `69df171` (flag cutover) on top of `312e1e8` (Step 1 handoff)
**Status:** ✅ **Code-side cutover complete. Bot restart required to activate.**

You said "flip everything, make it work as intended, i override" — I did exactly that. Here is the state.

---

## What Is Live As Of This Commit

### Flags (config.py + safety_overlay defaults — both layers flipped)

| Flag | Before Tonight | Now | What It Does |
|---|---|---|---|
| `USE_APEX_V3_SHADOW` | False | **True** | Shadow + divergence logs keep writing |
| `USE_LEGACY_PIPELINE` | True | **False** | Scan-cycle Track A routes through Apex (`_run_apex_pipeline(execute=True)`) |
| `PM_LEGACY_OPUS_REVIEW_ENABLED` | True | **False** | PM Track B routes through Apex (`dispatch(..., execute=True)`) |
| `SENTINEL_LEGACY_PIPELINE_ENABLED` | True | **False** | Sentinel NEWS_INTERRUPT routes through Apex |
| `TRADE_ADVISOR_ENABLED` | True | **False** | Deterministic sizing only (ATR + conviction_mult) |
| `FINBERT_MATERIALITY_GATE_ENABLED` | False | **True** | News materiality gate reads `finbert_confidence` (≥4) |

### Model
`claude_model_alpha = claude-sonnet-4-6` (per your "sonnet" answer — not Opus).

### Legacy Code Status
**Preserved, not deleted.** Per your earlier standing directive and basic rollback hygiene, every legacy path (`run_portfolio_review`, `run_sentinel_pipeline`, `trade_advisor.advise_trade`, agents.py) is still in the repo, gated behind its flag. Flipping any flag back in `config.py` restores legacy for that subsystem with zero code change.

---

## Activation Procedure (You Have To Do This)

The bot is a long-running process. It reads `config.py` at startup, so flag changes only take effect on restart.

1. **Stop the running bot.** Whatever way you normally stop it (Ctrl+C on the tmux/terminal, or `pkill -f "python.*bot.py"`).
2. **Confirm it's down:** `ps aux | grep -E "bot\.py|bot_trading" | grep -v grep` should return nothing.
3. **Verify config diff:**
   ```
   cd "/Users/amitchopra/Desktop/decifer trading"
   git show 69df171 -- config.py
   ```
   Expect the six flag lines changed exactly as above. No other change.
4. **Start the bot** (same way you started it tonight).
5. **Tail logs for 1–2 scan cycles:**
   ```
   tail -f logs/decifer.log | grep -E "APEX_LIVE|APEX_SHADOW|APEX_IMPORT|APEX_DISPATCH|apex_orchestrator"
   ```
   Expect lines starting `APEX_LIVE SCAN_CYCLE:` (the new cutover branch's summary log).

---

## What Will Happen On The First Post-Restart Scan

- `run_scan()` reaches the Track A cutover guard.
- `_scan_cutover = not should_use_legacy_pipeline()` evaluates **True**.
- Legacy `_all_buys` buy-loop is **skipped**.
- `apex_orchestrator._run_apex_pipeline(execute=True)` builds the SCAN_CYCLE ApexInput, calls Sonnet, validates schema, runs guardrails' semantic filter, and calls `signal_dispatcher.dispatch(..., execute=True)` for new_entries and portfolio_actions, plus `dispatch_forced_exit(..., execute=True)` for any forced exits from `screen_open_positions()`.
- Any error in the Apex path is swallowed into `dispatch_report["errors"]` — the bot does not crash. In that case the cycle is a no-op (**no legacy fallback** — that was a deliberate design choice; legacy is disabled, not available as a silent backstop).

**For News Sentinel:** Materiality gate now reads `finbert_confidence ≥ 4` (or `urgency == "CRITICAL"`). The Claude sentiment Sonnet call that used to feed this gate is no longer on the critical path for firing — the triggered path instead builds an ApexInput via `sentinel_agents.build_news_trigger_payload()` and calls the Apex.

**For Portfolio Manager:** Track B review flags positions via `guardrails.flag_positions_for_review()`, builds an ApexInput, and dispatches TRIM/EXIT/HOLD decisions from Apex directly.

---

## Test State

- **Full regression:** `pytest tests/ -q` → **2064 passed, 0 failed, 1 skipped** (run: 182s).
- 20 flag-default assertion tests updated: either inverted to the new defaults, or (for the flip_proposer forward-walk tests) given a `legacy_flag_state` fixture so they keep testing the proposer tool's semantics from a simulated legacy starting state.
- No code logic changed in the cutover commit — only flag values and test expectations. The actual execute-path plumbing landed earlier tonight in Phase 8A commits (`3b61141`, `799f425`, `eb0cdcd`).

---

## What You Should Watch For Tomorrow

1. **First-trade-per-symbol correctness.** On the first real BUY that fires through the cutover, check that `orders_core.execute_buy` received a sane `qty`, `sl`, `tp` — i.e., the `CONVICTION_MULT[conviction] * ATR` sizing path worked and Apex produced a valid `conviction` field. `data/trades.json` will show the new trade; compare against `data/apex_shadow_log.jsonl` for the matching decision.
2. **Schema rejects.** If `apex_call()` returns `_fallback_decision()` (empty entries + all HOLDs), the cycle silently does nothing. Grep for `fallback` in `data/apex_shadow_log.jsonl`'s `apex_meta.attempts` > 1 or `decision._meta`. Should be rare.
3. **Forced exits still fire.** The cutover path *does* still call `screen_open_positions()` and dispatch forced exits (EOD flat, 90-min scalp timeout, long-only-in-SHORT, UNKNOWN trade_type). Those do not go through Apex — they execute directly via `dispatch_forced_exit(..., execute=True)`.
4. **`APEX_LIVE SCAN_CYCLE:` log line per scan.** If it's missing, the cutover guard didn't trigger and the bot is still on legacy. Check you actually restarted.

---

## Rollback (If Anything Looks Wrong)

One file, six lines, one restart:

```
cd "/Users/amitchopra/Desktop/decifer trading"
git revert --no-edit 69df171
# Bot restart
```

That restores all six flags to legacy and you are back to exactly where you were before tonight's cutover commit.

---

## What Did NOT Happen Tonight (Deliberately)

- **Legacy code deletion.** Per your earlier standing directive "Do NOT delete any legacy code tonight" — and because keeping it means rollback is one commit. The master-plan Phases 1/3/4/5/6 (rewrite `market_intelligence.py` into apex-only, delete `agents.py` four-agent code, delete `trade_advisor.py`, delete Opus call in `portfolio_manager.py`, etc.) are the follow-up job. They should be done with you watching, during market hours, after the cutover has run for at least a day with no errors. That is a supervised refactor, not an overnight one.
- **Phase 7B hard-gate verification.** The design called for ≥20 shadow cycles and gate checks before flipping. You explicitly overrode that ("i override and give you permission to go"). The override is noted. If the cutover misbehaves, that's the reason — and the revert above gets you back.
- **Model change to Opus.** You answered "sonnet." Kept at sonnet-4-6.

---

## Commit Chain Tonight

```
3b61141  refactor(apex): 8A.1 — _run_apex_pipeline(execute=True) implemented
799f425  refactor(apex): 8A.2 — scan-cycle Track A cutover branch
eb0cdcd  test(apex):     8A.3-8A.6 — PM/Sentinel/FinBERT lock-in tests
4fdf620  docs(apex):     8A handoff doc
312e1e8  docs(apex):     Phase 8 Step 1 verification handoff
69df171  refactor(apex): Phase 8 cutover — flip all five remaining flags  ← CURRENT
```

Decifer 3.0 is ready. Restart the bot to activate it.

— Claude
