# Intelligence Sprint Tests — Archive

Sprint-phase development checkpoint tests moved here during global production standardisation (2026-05-10).

These 20 test files verified in-progress intelligence pipeline feature work (Sprints 2–7c, day-by-day checkpoints). They are **not** permanent regression guards — they captured the state of the codebase at specific sprint milestones.

## Recovery

To restore: `git mv tests/archive/intelligence_sprint_tests/<file> tests/` and commit.
Do not restore to the active test suite without verifying the tests still pass against current production code.

## What's Here

- `test_intelligence_day2.py` through `test_intelligence_day7.py` — day-by-day progress checks
- `test_intelligence_sprint2.py` through `test_intelligence_sprint7c.py` — sprint milestone checks
- `test_intelligence_factor_registry.py` — factor registry dev checkpoint
- `test_intelligence_reference_data.py` — reference data dev checkpoint
