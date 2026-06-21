"""
compare.py — line up all logged runs side by side.

Reads runs/*/summary.json and prints a comparison table. This is the payoff:
A/B Opus vs Sonnet, or before/after mouse-off, by the numbers.

    python compare.py
"""

import os
import json
import glob

rows = []
for s in sorted(glob.glob("runs/*/summary.json")):
    d = json.load(open(s))
    d["run"] = os.path.basename(os.path.dirname(s))
    rows.append(d)

if not rows:
    raise SystemExit("no runs found — run the logged loop first")

cols = ["run", "model", "finished", "iters", "reclicks",
        "wall_s", "in_tok", "out_tok", "est_cost_usd"]
w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}

header = "  ".join(c.ljust(w[c]) for c in cols)
print(header)
print("-" * len(header))
for r in rows:
    print("  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))
