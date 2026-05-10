# Archive

Modules moved here during the global production standardisation audit (2026-05-10).
These are **not** in the live bot runtime closure and have no permanent test coverage.

## Contents

| File | Reason |
|------|--------|
| `backtester.py` | Backtest tooling only |
| `backtest_intelligence.py` | Backtest tooling only |
| `build_brain.py` | ML model build tool |
| `compare_universes.py` | One-shot comparison script |
| `paper_handoff_builder.py` | Pre-cutover shadow comparator |
| `paper_handoff_comparator.py` | Pre-cutover shadow comparator |
| `apex_divergence.py` | Pre-cutover shadow divergence logger |
| `reachability.py` | Dead-code analysis tool (itself unused) |
| `audit_candle_gate.py` | Standalone audit utility |
| `daily_journal.py` | Standalone journal generator |

## Recovery

To restore any file: `git mv archive/<file> .` and commit.
Do not restore without updating `docs/production_runtime_surface.md`.
