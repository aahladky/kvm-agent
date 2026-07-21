"""shakedown_ab.py -- the 17-task calibration batch (notepad/windows_calc/
microsoft_paint/clock/settings = 2+3+3+4+5 = 17), run once per HOLO_HISTORY_IMAGES
depth in {1,2,3}, for morning review (2026-07-19: history=1 vs 2 both scored 1.0 on
the single notepad task post-focus-fix; this is the real, multi-task follow-up).

Writes each category's result JSON (as produced by waa/runner.py) into
waa/shakedown_results/h<depth>_<category>_<original-filename>.json, and keeps
waa/shakedown_results/manifest.json updated after every category so progress is
visible without waiting for the whole thing to finish.

    python tools/shakedown_ab.py
    python tools/shakedown_ab.py --depths 1 2 3      # default
    python tools/shakedown_ab.py --depths 2          # just one depth
"""
import argparse
import json
import os
import shutil
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "/home/aaron/workspace/WindowsAgentArena/.venv/bin/python3"
CATEGORIES = ["notepad", "windows_calc", "microsoft_paint", "clock", "settings"]
RESULTS_DIR = os.path.join(REPO, "waa", "results")
OUT_DIR = os.path.join(REPO, "waa", "shakedown_results")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")


def latest_result_file(after_ts):
    candidates = [f for f in os.listdir(RESULTS_DIR) if f.startswith("waa_") and f.endswith(".json")]
    candidates = [f for f in candidates if os.path.getmtime(os.path.join(RESULTS_DIR, f)) >= after_ts]
    if not candidates:
        return None
    return max(candidates, key=lambda f: os.path.getmtime(os.path.join(RESULTS_DIR, f)))


def load_manifest():
    if os.path.exists(MANIFEST_PATH):
        return json.load(open(MANIFEST_PATH))
    return []


def save_manifest(entries):
    with open(MANIFEST_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def run_category(depth, category):
    env = dict(os.environ)
    env["HOLO_HISTORY_IMAGES"] = str(depth)
    t0 = time.time()
    print(f"\n=== history={depth} category={category} starting {time.strftime('%H:%M:%S')} ===", flush=True)
    proc = subprocess.run(
        [PY, "waa/runner.py", "--category", category, "--max-steps", "40"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    dt = time.time() - t0
    print(proc.stdout[-4000:], flush=True)
    if proc.returncode != 0:
        print(f"!!! history={depth} category={category} nonzero exit {proc.returncode}:\n{proc.stderr[-3000:]}",
              flush=True)

    fn = latest_result_file(t0)
    dst = None
    n_pass = n_total = None
    if fn:
        src = os.path.join(RESULTS_DIR, fn)
        dst = os.path.join(OUT_DIR, f"h{depth}_{category}_{fn}")
        shutil.copy(src, dst)
        try:
            rows = json.load(open(src))
            n_total = len(rows)
            n_pass = sum(1 for r in rows if r.get("score", 0) >= 1.0)
        except Exception:
            pass
    else:
        print(f"!!! history={depth} category={category}: no results/waa_*.json found after start "
              f"-- runner may have crashed before writing output", flush=True)

    print(f"=== history={depth} category={category} done in {dt:.1f}s "
          f"({n_pass}/{n_total} passed) -> {dst} ===", flush=True)
    return {"history": depth, "category": category, "wall_s": round(dt, 1),
            "returncode": proc.returncode, "result_file": dst,
            "n_pass": n_pass, "n_total": n_total}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depths", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--categories", nargs="+", default=CATEGORIES)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = load_manifest()
    t_start = time.time()

    try:
        for depth in args.depths:
            for cat in args.categories:
                entry = run_category(depth, cat)
                manifest.append(entry)
                save_manifest(manifest)
    finally:
        total_s = time.time() - t_start
        print(f"\n\nSHAKEDOWN COMPLETE in {total_s:.0f}s ({total_s/60:.1f} min) "
              f"-> {MANIFEST_PATH}", flush=True)
        by_depth = {}
        for e in manifest:
            d = by_depth.setdefault(e["history"], {"pass": 0, "total": 0})
            if e["n_pass"] is not None:
                d["pass"] += e["n_pass"]
                d["total"] += e["n_total"]
        for depth, agg in sorted(by_depth.items()):
            print(f"  history={depth}: {agg['pass']}/{agg['total']} passed")


if __name__ == "__main__":
    main()
