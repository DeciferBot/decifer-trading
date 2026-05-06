#!/usr/bin/env python3
"""
CLI wrapper for intelligence_schema_validator.

Validates all Intelligence-First files including Sprint 4A outputs
(daily_economic_state.json, current_economic_context.json) when present.

Usage:
    python scripts/validate_intelligence_files.py
    python scripts/validate_intelligence_files.py --dir data/intelligence

Exits 0 on all-pass, 1 if any file has errors.
"""

import argparse
import sys
import os

# Allow imports from repo root regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intelligence_schema_validator import validate_all


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Intelligence-First static files")
    parser.add_argument("--dir", default="data/intelligence", help="Directory containing intelligence JSON files")
    args = parser.parse_args()

    results = validate_all(args.dir)
    any_failed = False

    for label, result in results.items():
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {label}")
        for err in result.errors:
            print(f"  ERROR: {err}")
            any_failed = True
        for warn in result.warnings:
            print(f"  WARN:  {warn}")

    if any_failed:
        print("\nValidation FAILED — fix errors above before proceeding.")
        return 1

    print("\nAll intelligence files valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
