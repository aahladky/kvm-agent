"""
measure.py — honest reliability measurement for the executive (rates, not anecdotes).

The prior investigation's cardinal sin was drawing conclusions from single temp-0 runs.
This runs K reps of the multi-app task with RANDOMIZED operands, auto-resets to a clean
desktop between reps, and VERIFIES each rep from the screen with the vision model (never
self-report). It reports a verified success RATE + timing.

Run from a REPL that already holds the rig:
    import measure, importlib; importlib.reload(measure)
    M = measure.multiapp(EX, K=10)          # assign (don't echo) ; prints per-rep + summary
Or standalone:
    python measure.py --k 10
"""
import os, time, json, random


def multiapp(ex, K=10, seed=0, ops=("+", "*"), log_dir=r"C:\Dev\vllm\runs", tag=None):
    random.seed(seed)
    tag = tag or time.strftime("measure_%Y%m%d_%H%M%S")
    out = os.path.join(log_dir, tag)
    os.makedirs(out, exist_ok=True)
    rows, t_all = [], time.time()

    for i in range(K):
        # ---- clean start: vision-gated close until the desktop is verified clear
        #      (robust to identical stacked windows; self-correcting on a close-race) ----
        ex.reset_clean(max_close=12)

        a, b = random.randint(11, 99), random.randint(11, 99)
        op = random.choice(ops)
        expect = a + b if op == "+" else a * b
        expr = f"{a}{op}{b}"
        plan = [
            {"op": "launch", "app": "notepad"},
            {"op": "type", "text": "milk, eggs, and bread"},
            {"op": "verify", "expect": "milk"},
            {"op": "launch", "app": "calc"},
            {"op": "type", "text": expr},
            {"op": "tap", "key": "enter"},
            {"op": "verify", "number==": str(expect)},
            {"op": "done"},
        ]
        t0 = time.time()
        try:
            res = ex.run_plan(plan, goal=f"notepad + {expr}", run_tag=f"{tag}_rep{i:02d}")
            status = res["status"]
        except Exception as e:
            res, status = {"status": f"exception:{e!r}", "log": []}, "exception"

        # what the vision verifier read off the calc display (for auditing the metric)
        got = next((r.get("got") for r in res.get("log", []) if r.get("op") == "verify"
                    and "got" in r), None)
        notep = next((r.get("verify") for r in res.get("log", []) if r.get("op") == "verify"
                      and "verify" in r), None)
        ok = status == "done"
        dt = round(time.time() - t0, 1)

        # save the final frame so the rate can be spot-audited against ground truth
        try:
            with open(os.path.join(out, f"rep{i:02d}_final.png"), "wb") as f:
                f.write(ex.observe())
        except Exception:
            pass

        rows.append({"i": i, "expr": expr, "expect": expect, "got": got,
                     "notepad_ok": notep, "status": status, "ok": ok, "secs": dt})
        print(f"  rep{i:02d}  {expr}={expect:<5} read={got!s:<6} notepad={notep!s:<5} "
              f"{'PASS' if ok else 'FAIL '+status:<12}  {dt}s")

    passes = sum(r["ok"] for r in rows)
    summary = {"K": K, "passes": passes, "rate": round(passes / K, 3),
               "mean_secs": round(sum(r["secs"] for r in rows) / K, 1),
               "wall_secs": round(time.time() - t_all, 1), "rows": rows, "tag": tag}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=2)
    print(f"\n  ===== {passes}/{K} verified PASS  ({summary['rate']*100:.0f}%)  "
          f"mean {summary['mean_secs']}s/task  wall {summary['wall_secs']}s =====")
    print(f"  frames+log: {out}")
    return summary


if __name__ == "__main__":
    import argparse
    from executive import Executive, Verifier
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--executor", default="uitars-q4")
    ap.add_argument("--vision", default="qwen2.5vl:7b")
    args = ap.parse_args()
    # capture=False: this harness measures wall-clock timing and saves its own final frames;
    # per-step frame capture would add encode/disk overhead and skew the rate/timing baseline.
    ex = Executive.open(executor_model=args.executor,
                        verifier=Verifier(vision_model=args.vision), capture=False)
    try:
        multiapp(ex, K=args.k)
    finally:
        ex.close()
