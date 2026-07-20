"""diag_clicks.py — what actually happened on the CLICK steps of the live runs?

Reads plan.json from runs/<firefox*>/ and dumps, per click step: target, grounded xy, ok, and the
grounder's raw output + the instructions it tried. Isolates the click failure mode (no-coordinate
vs misground vs false change-detection) from real data, before we touch grounding.

    python tools\diag_clicks.py
    python tools\diag_clicks.py firefox        # dir glob prefix (default 'firefox')
"""
import os, sys, json, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kvm_agent.config import CFG

ROOT = CFG.runs_dir
pref = sys.argv[1] if len(sys.argv) > 1 else "firefox"
dirs = sorted(glob.glob(os.path.join(ROOT, pref + "*")), key=os.path.getmtime)

n_clicks = n_nocoord = n_xy_failed = n_xy_ok = n_abstain = 0
for d in dirs:
    pj = os.path.join(d, "plan.json")
    if not os.path.isdir(d) or not os.path.exists(pj):
        continue
    try:
        r = json.load(open(pj))
    except Exception as e:
        print(os.path.basename(d), "-> plan.json load error:", e)
        continue
    clicks = [x for x in r.get("log", []) if x.get("op") == "click"]
    if not clicks:
        continue
    print(f"\n=== {os.path.basename(d)}  | status={r.get('status')} ===")
    for c in clicks:
        n_clicks += 1
        g = c.get("ground") or {}
        tried = g.get("attempts", [])
        gx = g.get("xy")          # what the GROUNDER returned (None only if it truly declined)
        cx = c.get("xy")          # click_target result (None once retries are exhausted)
        ok = c.get("ok")
        ver = g.get("verified")   # pre-click gate verdict (None on pre-gate runs)
        if gx is None:
            n_nocoord += 1; kind = "NO-COORD (grounder declined)"
        elif ver is False:
            n_abstain += 1; kind = "ABSTAINED (gate: not the target / wrong screen)"
        elif ok:
            n_xy_ok += 1; kind = "clicked + effect (ok)"
        else:
            n_xy_failed += 1; kind = "clicked but NO effect"
        print(f"  step {c.get('i')}: target={c.get('step', {}).get('target')!r}")
        print(f"     -> grounder_xy={gx} click_xy={cx} ok={ok} verified={ver}  [{kind}]")
        for t in tried:
            acts = t.get("actions")
            raw = (t.get("raw") or "").replace(chr(10), " ")[:200]
            print(f"        instr={t.get('instruction')!r}")
            print(f"        actions={acts}  raw={raw!r}")

print(f"\n--- totals: clicks={n_clicks}  no_coord={n_nocoord}  gate_abstained={n_abstain}  "
      f"clicked_no_effect={n_xy_failed}  clicked_ok={n_xy_ok}")
