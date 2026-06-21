"""
operate.py — minimal interactive operator for the EvoCUA rig.

Hand it a natural-language goal and it runs one S2 rollout against the physical
target until the model terminates (or a safety hook / Ctrl+C stops it). Unlike
run_probe.py (a benchmark harness), there is NO test coupling here: no expected
answer, no OCR verify-gate, no calculator-specific reset. The goal string IS the
prompt; the S2 system prompt + tool schema come from EvoCUAAgent.

Usage:
    python operate.py                      # REPL: boots the rig, then a `goal>` prompt
    python operate.py "open Calculator"    # run that goal first, then drop into the REPL
    python operate.py --once "do X"        # run one goal and exit (scriptable)
    python operate.py --confirm            # approve each action before the Pico fires it

In the REPL, Ctrl+C aborts the CURRENT goal and returns to the prompt; an empty line,
'quit', or Ctrl+D exits and releases the hardware.

Default is FREE-RUN (suitable for the hardware sandbox). Use --confirm on any machine
you care about. run_probe.py stays the benchmark / re-baseline harness.

[2-way street, later] This one-way loop (you give a goal, it acts) is the seed of a
bidirectional one. The attach points are marked TODO(2way): the agent's S2 `answer`
action is where the model would surface a question back to you, and the per-step gate
(--confirm) is where mid-run user steering would inject a corrective instruction
(reuse run_probe's corrective_instruction()).
"""
import os
import re
import sys
import time
import argparse

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
sys.path.insert(0, REPO)
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

from evocua_agent import EvoCUAAgent   # our patched copy at root  # noqa: E402
from pico_env import PicoEnv           # noqa: E402

# Defaults = the proven S2 deployment config (matches run_probe's S2 entry).
MODEL        = "evocua-8b-q5-clean"
PROMPT_STYLE = "S2"
COORD_TYPE   = "relative"
RESIZE       = 32
HISTORY      = 4
TEMPERATURE  = 0.01
MAX_TOKENS   = 2048
SETTLE       = 1.0
MAX_STEPS    = 25
MAX_EMPTY_STREAK = 3    # consecutive zero-action steps -> abort (format/parse regression guard)


def extract_xy(cmd):
    m = re.search(r"pyautogui\.(?:click|moveTo|doubleClick|tripleClick|rightClick)\(\s*"
                  r"(?:x\s*=\s*)?(-?\d+)\s*,\s*(?:y\s*=\s*)?(-?\d+)", str(cmd))
    return (int(m.group(1)), int(m.group(2))) if m else None


def run_goal(agent, env, goal, confirm=False, settle=SETTLE, max_steps=MAX_STEPS):
    """Run ONE rollout for `goal`. Returns 'DONE' | 'FAIL' | 'ABORT' | None (max steps)."""
    agent.reset()
    obs = env.observe()          # current screen; NO physical reset click
    recent, empty_streak = [], 0
    t0 = time.time()

    for it in range(max_steps):
        try:
            response, actions = agent.predict(goal, obs)
        except Exception as e:
            print(f"  [{it}] predict error: {e}")
            return None

        # dropped-action guard (same failure class the tool_call fix targets).
        # PATCH(answer-channel): a parsed answer is a real step, not a dropped action -- don't
        # count it toward the empty streak (it also always carries a token, so this rarely
        # matters, but it keeps the guard honest about communicative turns).
        if not actions and not getattr(agent, "last_answer", None):
            empty_streak += 1
            print(f"  [{it}] (no action parsed; streak {empty_streak}) :: {(response or '')[:80]!r}")
            if empty_streak >= MAX_EMPTY_STREAK:
                print("  too many empty steps -> abort")
                return "ABORT"
            continue
        empty_streak = 0

        for action in actions:
            if action in ("DONE", "FAIL"):
                rep = getattr(agent, "last_answer", None)
                if rep:
                    print(f"        model's reported answer: {rep!r}")
                print(f"  [{it}] TERMINATE -> {action}   ({time.time()-t0:.1f}s)")
                return action
            if action == "ANSWER":  # PATCH(answer-channel): the 2-way street
                q = getattr(agent, "last_answer", None) or ""
                print(f"  [{it}] MODEL ASKS / REPORTS -> {q!r}")
                obs, _, _, _ = env.step(action, settle)   # no-op step; refreshes obs
                try:
                    reply = input("        your reply (Enter to skip): ").strip()
                except EOFError:
                    reply = ""
                if reply:
                    goal = f"{goal}\n\n[user reply] {reply}"   # folded into the next predict
                continue
            if action == "WAIT":
                print(f"  [{it}] WAIT")
                obs, _, _, _ = env.step(action, settle)
                continue
            xy = extract_xy(action)
            tag = f"   @ {xy}" if xy else ""
            print(f"  [{it}] {action}{tag}")
            if confirm:  # TODO(2way): a typed instruction here could steer mid-run
                ans = input("        fire? [Enter=yes / s=skip / q=quit] ").strip().lower()
                if ans == "q":
                    return "ABORT"
                if ans == "s":
                    continue
            obs, _, _, _ = env.step(action, settle)

        # free-run loop guard: 6 recent clicks collapsing to <=2 spots = stuck
        xy = extract_xy(actions[-1])
        if xy:
            recent = (recent + [xy])[-6:]
            if len(recent) >= 6:
                clusters = []
                for c in recent:
                    if not any(abs(c[0]-k[0]) < 15 and abs(c[1]-k[1]) < 15 for k in clusters):
                        clusters.append(c)
                if len(clusters) <= 2:
                    print("  re-click loop -> abort")
                    return "ABORT"

    print(f"  max steps ({max_steps}) reached without terminate")
    return None


def main():
    ap = argparse.ArgumentParser(description="Interactive operator for the EvoCUA rig.")
    ap.add_argument("goal", nargs="*", help="initial goal (optional); joined into one string")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--history", type=int, default=HISTORY)
    ap.add_argument("--settle", type=float, default=SETTLE)
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    ap.add_argument("--confirm", action="store_true", help="approve each action before it fires")
    ap.add_argument("--once", action="store_true", help="run the initial goal, then exit (no REPL)")
    args = ap.parse_args()

    agent = EvoCUAAgent(
        model=args.model, max_tokens=MAX_TOKENS, top_p=0.9, temperature=TEMPERATURE,
        action_space="pyautogui", observation_type="screenshot", max_steps=args.max_steps,
        prompt_style=PROMPT_STYLE, max_history_turns=args.history,
        screen_size=(1920, 1080), coordinate_type=COORD_TYPE, resize_factor=RESIZE,
    )
    env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)

    def do(goal):
        goal = goal.strip()
        if not goal:
            return
        print(f"\n=== goal: {goal!r} ===")
        try:
            res = run_goal(agent, env, goal, confirm=args.confirm,
                           settle=args.settle, max_steps=args.max_steps)
            print(f"=== result: {res} ===")
        except KeyboardInterrupt:
            try:
                env.r4.up()   # safety: release any held button before returning to prompt
            except Exception:
                pass
            print("\n  [interrupted] goal aborted; back to prompt")

    try:
        initial = " ".join(args.goal).strip()
        if initial:
            do(initial)
            if args.once:
                return
        while True:   # simple REPL (the seed of the future 2-way street)
            try:
                goal = input("\ngoal> ")
            except (EOFError, KeyboardInterrupt):
                break
            if goal.strip().lower() in ("", "quit", "exit", "q"):
                break
            do(goal)
    finally:
        env.close()
        print("hardware released. bye.")


if __name__ == "__main__":
    main()
