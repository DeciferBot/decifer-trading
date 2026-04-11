#!/usr/bin/env python3
"""
<> Decifer — patch.py

Apply code fixes without stopping the bot.
The hot reload system picks up changes automatically on the next scan.

Usage:
    python3 patch.py <module> "<old_code>" "<new_code>"

Or for dashboard fixes (no restart needed, just browser refresh):
    python3 patch.py dashboard "<old>" "<new>"

Examples:
    python3 patch.py config '"risk_pct_per_trade": 0.02' '"risk_pct_per_trade": 0.03'
    python3 patch.py agents 'agents_required: int' 'agents_required: int = 4'
"""

import sys
import os

def patch(module: str, old: str, new: str):
    path = os.path.join(os.path.dirname(__file__), f"{module}.py")
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        sys.exit(1)

    with open(path) as f:
        content = f.read()
    if old not in content:
        print(f"❌ Pattern not found in {module}.py")
        print(f"   Looking for: {old[:80]}...")
        sys.exit(1)

    count   = content.count(old)
    content = content.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(content)
    print(f"✅ Patched {module}.py — {count} replacement(s)")

    if module == "dashboard":
        print("   → Hard refresh browser (Cmd+Shift+R) to see changes")
    else:
        print("   → Bot will hot reload on next scan (within 5 minutes)")
        print("   → Watch for 🔄 indicator in dashboard header")

if __name__ == "__main__":
    if len(sys.argv) == 4:
        patch(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
