r"""
probe_planner.py — see the PLANNER's raw output for a goal, with NO HID execution.

Isolates planner quality from the executive/HID path: constructs the configured planner
(default HFPlanner = Qwen3-VL-8B), calls decompose() on a goal, and dumps the RAW model
reply + the parsed plan + validate_plan()'s cleaned plan/issues + whether it is actionable.
Optionally feeds a saved screenshot (--frame PATH) so the plan reflects real on-screen
state; text-only otherwise (no rig/camera needed).

    python tools\probe_planner.py "Download and install firefox then set it as the default browser"
    python tools\probe_planner.py --kind hf --frame runs\some\00_launch.png "..."
    python tools\probe_planner.py --kind claude "..."
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # recalled/model text may be non-cp1252
except Exception:
    pass
from kvm_agent.config import CFG
from kvm_agent.orchestration.planner import (HFPlanner, LocalPlanner, ClaudePlanner, RulePlanner,
                                             validate_plan, validate_step, plan_is_actionable)


def build(kind, send_image):
    kind = (kind or CFG.planner_kind).lower()
    mt = CFG.planner_effective_max_tokens
    if kind == "claude":
        return ClaudePlanner(api_key=CFG.anthropic_key or None, max_tokens=mt,
                             thinking=CFG.planner_thinking)
    if kind == "rule":
        return RulePlanner()
    if kind == "local":   # OpenAI-compatible endpoint, e.g. the B580 llama-server on :8080
        return LocalPlanner(model=CFG.planner_model, base_url=CFG.planner_local_url,
                            send_image=send_image, max_tokens=mt, thinking=CFG.planner_thinking)
    return HFPlanner(model=CFG.planner_model, send_image=send_image, max_tokens=mt,
                     thinking=CFG.planner_thinking)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("goal")
    ap.add_argument("--kind", default=None, help="hf|claude|rule (default: CFG.planner_kind)")
    ap.add_argument("--frame", default=None, help="path to a PNG screenshot to feed the planner")
    ap.add_argument("--memory", action="store_true",
                    help="recall + inject Hindsight memory for the goal (A/B vs no-memory baseline)")
    ap.add_argument("--step", action="store_true",
                    help="ALSO probe the closed-loop next_step() single-action path (what run_goal_step uses)")
    args = ap.parse_args()

    png = None
    if args.frame:
        with open(args.frame, "rb") as f:
            png = f.read()
    send_image = png is not None
    planner = build(args.kind, send_image)
    print(f"[probe] planner={type(planner).__name__} model={getattr(planner,'model','-')} "
          f"send_image={send_image} frame={args.frame} memory={args.memory}")
    if args.memory:
        from kvm_agent.memory.hindsight import HindsightMemory
        block = HindsightMemory().recall_block(args.goal)
        planner.context = block or None
        print("\n===== RECALLED MEMORY (injected) =====\n" + (block or "(none recalled)"))
    try:
        raw_plan = planner.decompose(args.goal, png)
    except Exception as e:
        print(f"\n!!! decompose() RAISED: {e!r}")
        raw = getattr(planner, "last_raw", None)
        if raw:
            print("\n===== RAW MODEL REPLY (before parse failure) =====\n" + raw)
        sys.exit(1)
    raw = getattr(planner, "last_raw", None)
    clean, issues = validate_plan(raw_plan)
    print("\n===== RAW MODEL REPLY =====\n" + (raw if raw is not None else "(none / RulePlanner)"))
    print("\n===== PARSED PLAN =====\n" + json.dumps(raw_plan, indent=2))
    print("\n===== VALIDATED PLAN =====\n" + json.dumps(clean, indent=2))
    print("\n===== LINT ISSUES =====\n" + ("\n".join(issues) if issues else "(none)"))
    print(f"\nactionable={plan_is_actionable(clean)}  steps={len(clean)}")

    if args.step:
        try:
            step = planner.next_step(args.goal, png, [])
        except Exception as e:
            print(f"\n!!! next_step() RAISED: {e!r}")
            raw2 = getattr(planner, "last_raw", None)
            if raw2:
                print("\n===== RAW next_step REPLY =====\n" + raw2)
            sys.exit(1)
        cstep, sissues = validate_step(step)
        print("\n===== CLOSED-LOOP next_step (single action — what run_goal_step asks for) =====")
        print("raw:       " + str(getattr(planner, "last_raw", None)))
        print("parsed:    " + json.dumps(step))
        print("validated: " + json.dumps(cstep) + "   issues: " + (", ".join(sissues) or "(none)"))
