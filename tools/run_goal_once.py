"""
run_goal_once.py — run ONE goal end-to-end through planner->executive on the live rig.

A controlled single shot of planner.run_goal (what the Open WebUI server does per task), but
on the console with full output: streams progress, prints the final result + every plan tried,
and leaves per-step frames + planner.json + plan.json under runs/<tag>/.

Requires the rig FREE (camera + Pico) — stop agent_server first (single capture card).

    python tools\run_goal_once.py "Download and install firefox then set it as the default browser"
    python tools\run_goal_once.py --kind hf --executor uitars-q4 --max-replans 3 "..."
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # recalled/OCR text may be non-cp1252
except Exception:
    pass
from kvm_agent.config import CFG
from kvm_agent.orchestration.planner import (
    run_goal, run_goal_step, HFPlanner, LocalPlanner, ClaudePlanner, RulePlanner)
from kvm_agent.orchestration.executive import Executive, Verifier


def build(kind):
    kind = (kind or CFG.planner_kind).lower()
    mt = CFG.planner_effective_max_tokens
    if kind == "claude":
        return ClaudePlanner(api_key=CFG.anthropic_key or None, max_tokens=mt,
                             thinking=CFG.planner_thinking)
    if kind == "rule":
        return RulePlanner()
    if kind == "local":   # OpenAI-compatible endpoint, e.g. the B580 llama-server on :8080
        return LocalPlanner(model=CFG.planner_model, base_url=CFG.planner_local_url,
                            send_image=CFG.send_image, max_tokens=mt, thinking=CFG.planner_thinking)
    return HFPlanner(model=CFG.planner_model, max_tokens=mt, thinking=CFG.planner_thinking)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("goal")
    ap.add_argument("--kind", default=None, help="hf|claude|rule (default: CFG.planner_kind)")
    ap.add_argument("--executor", default=None, help="executor model (default: CFG.executor_model)")
    ap.add_argument("--max-replans", type=int, default=2)
    ap.add_argument("--closed-loop", action="store_true",
                    help="per-step closed loop (observe->act->observe) instead of plan+replan "
                         "(else CFG.closed_loop / AGENT_CLOSED_LOOP)")
    ap.add_argument("--max-steps", type=int, default=None,
                    help="closed-loop step budget (default: CFG.closed_loop_max_steps)")
    ap.add_argument("--plan", action="store_true",
                    help="force the OLD plan-then-replan run_goal, overriding --closed-loop / "
                         "AGENT_CLOSED_LOOP (for a clean A/B against the closed loop)")
    ap.add_argument("--no-reset", action="store_true", help="skip the reset-to-clean-desktop step")
    ap.add_argument("--tag", default="firefox")
    ap.add_argument("--memory", action="store_true",
                    help="recall + inject Hindsight memory for the goal (else CFG.hindsight_enabled)")
    ap.add_argument("--write", action="store_true",
                    help="write-back: retain the working recipe on success (else CFG.hindsight_write)")
    args = ap.parse_args()

    mem = None
    if args.memory or CFG.hindsight_enabled:
        from kvm_agent.memory.hindsight import HindsightMemory
        mem = HindsightMemory()

    planner = build(args.kind)
    use_closed = False if args.plan else (args.closed_loop or CFG.closed_loop)
    if isinstance(planner, RulePlanner) and use_closed:
        sys.exit("[run] ERROR: the per-step closed loop needs a MODEL planner (hf|claude) — "
                 "RulePlanner has no next_step. Re-run with --kind hf  (AGENT_PLANNER is 'rule' in "
                 "this shell; --kind overrides it).")
    if isinstance(planner, RulePlanner):
        print("[run] WARNING: planner=RulePlanner — it only handles notepad/calculator goals and "
              "returns [done] for anything else (that is the no-op you saw). Use --kind hf for a "
              "real goal like installing Firefox.")
    max_steps = args.max_steps or CFG.closed_loop_max_steps
    print(f"[run] planner={type(planner).__name__} model={getattr(planner,'model','-')} "
          f"executor={args.executor or CFG.executor_model} reset={not args.no_reset} "
          f"loop={('per-step(max %d)' % max_steps) if use_closed else 'plan+replan'}")
    ex = Executive.open(executor_model=args.executor, verifier=Verifier(CFG.verifier_model),
                        log_dir=CFG.runs_dir)
    try:
        if use_closed:
            r = run_goal_step(args.goal, planner, ex, max_steps=max_steps,
                              reset_first=not args.no_reset, tag=args.tag,
                              on_event=lambda m: print("  •", m, flush=True), memory=mem,
                              write_memory=args.write or CFG.hindsight_write)
        else:
            r = run_goal(args.goal, planner, ex, max_replans=args.max_replans,
                         reset_first=not args.no_reset, tag=args.tag,
                         on_event=lambda m: print("  •", m, flush=True), memory=mem,
                         write_memory=args.write or CFG.hindsight_write)
    finally:
        ex.close()
    print("\n===== RESULT =====")
    print("status :", r.get("status"))
    if r.get("loop") == "per-step":
        print("elapsed:", r.get("elapsed"), "s   steps:", r.get("steps"))
        print("run_dir:", r.get("run_dir"))
        print("\n--- executed trace ---\n" + json.dumps(r.get("trace", []), indent=2))
        print("\n--- history (per-turn observations) ---")
        for h in r.get("history", []):
            print("  -", h)
    else:
        print("elapsed:", r.get("elapsed"), "s   replans:", r.get("replans"))
        print("run_dir:", r.get("run_dir"))
        for i, plan in enumerate(r.get("plans", [])):
            print(f"\n--- plan attempt {i} ---\n" + json.dumps(plan, indent=2))
