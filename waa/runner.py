"""WAA runner: WindowsAgentArena tasks + deterministic evaluators against OUR rig.

Setup and evaluation go through the WAA in-VM server (Flask :5000, installed in
win11-agent 2026-07-18); OBSERVATION and ACTION go through our production KVM path
(capture card + Pi 5/Pico HID appliance), so tasks measure the agent exactly as it
operates in production -- no in-guest pyautogui execution.

    python waa/runner.py --list [category]
    python waa/runner.py --category notepad                      # all tasks in a category
    python waa/runner.py --category notepad --task-id <uuid>     # one task
    python waa/runner.py --category notepad --max-steps 30 --no-reset

Task setup ("config") runs after a clean-desktop VM revert; evaluation mirrors
desktop_env.DesktopEnv.evaluate() exactly (postconfig -> getters -> metrics with
conj/options), reusing WAA's own getter/metric functions unmodified.
"""
import argparse
import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAA_CLIENT = "/home/aaron/workspace/WindowsAgentArena/src/win-arena-container/client"
WAA_TASKS = os.path.join(WAA_CLIENT, "evaluation_examples_windows", "examples")
sys.path.insert(0, WAA_CLIENT)
sys.path.insert(0, REPO_ROOT)

import requests  # noqa: E402
from desktop_env.controllers.setup import SetupController  # noqa: E402
from desktop_env.controllers.python import PythonController  # noqa: E402
from desktop_env.evaluators import metrics, getters  # noqa: E402

VM_IP = os.environ.get("WAA_VM_IP", "192.168.122.12")
# WAA tasks are written for their golden image, whose user is "Docker"; ours is "sandbox".
# Setup paths and evaluator file getters both reference the user profile, so rewrite the
# user-profile prefix at load time.
WIN_USER = os.environ.get("WAA_WIN_USER", "sandbox")
CACHE_DIR = os.path.join(REPO_ROOT, "waa", "cache")
RESULTS_DIR = os.path.join(REPO_ROOT, "waa", "results")


class WAAEnvShim:
    """The only surface WAA getters/metrics touch on `env`: .controller and
    .setup_controller (verified by grepping desktop_env/evaluators/getters)."""

    def __init__(self, vm_ip=VM_IP):
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.cache_dir = CACHE_DIR   # getters download VM files into env.cache_dir
        self.controller = PythonController(vm_ip=vm_ip)
        self.setup_controller = SetupController(vm_ip=vm_ip, cache_dir=CACHE_DIR)


def wait_for_server(timeout=120):
    """The WAA server autostarts at Windows logon; after a revert+cold boot it needs
    a few seconds to come up."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"http://{VM_IP}:5000/probe", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError(f"WAA server not reachable at {VM_IP}:5000 after {timeout}s")


def evaluate(env, task):
    """Mirror of DesktopEnv.evaluate() (desktop_env/envs/desktop_env.py), minus the
    action_history FAIL handling (our loop has no FAIL action; infeasible tasks are
    skipped upstream for now)."""
    ev = task["evaluator"]
    env.setup_controller.setup(ev.get("postconfig", []))

    func_list = ev["func"] if isinstance(ev["func"], list) else [ev["func"]]
    metric_fns = [getattr(metrics, f) for f in func_list]
    conj = ev.get("conj", "and")
    options = ([opt if opt else {} for opt in ev["options"]]
               if isinstance(ev.get("options", {}), list)
               else ev["options"] if "options" in ev else [{}] * len(metric_fns))
    results_cfg = ev.get("result", [])
    expected_cfg = ev.get("expected", [])
    if not isinstance(results_cfg, list):
        results_cfg = [results_cfg]
    if not isinstance(expected_cfg, list):
        expected_cfg = [expected_cfg] * len(metric_fns) if expected_cfg else [None] * len(metric_fns)

    results = []
    for idx, metric in enumerate(metric_fns):
        try:
            rget = getattr(getters, "get_{:}".format(results_cfg[idx]["type"]))
            result_state = rget(env, results_cfg[idx])
        except FileNotFoundError:
            if conj == "and":
                return 0.0, "file not found"
            results.append(0.0)
            continue
        except Exception as e:
            return 0.0, f"getter error: {e}"

        eget = None
        if expected_cfg[idx]:
            eget = getattr(getters, "get_{:}".format(expected_cfg[idx]["type"]))
        expected_state = eget(env, expected_cfg[idx]) if eget else None
        score = (metric(result_state, expected_state, **options[idx])
                 if expected_state is not None
                 else metric(result_state, **options[idx]))
        if conj == "and" and float(score) == 0.0:
            return 0.0, "conjunct failed"
        if conj == "or" and float(score) == 1.0:
            return 1.0, "disjunct satisfied"
        results.append(float(score))
    return (sum(results) / len(results) if conj == "and" else max(results)), "ok"


def run_task(task, max_steps=40, reset=True, record=True):
    import agent_loop_holo as loop
    loop.boot()
    if reset:
        from kvm_agent.hardware.vm import VMController
        VMController().revert_clean(capture_fn=loop._frame_png_full)
    wait_for_server()
    env = WAAEnvShim()
    if task.get("config"):
        env.setup_controller.setup(task["config"])
    t0 = time.time()
    res = loop.run(task["instruction"], max_steps=max_steps, confirm_first=0,
                   record=record, tag=f"waa__{task['id']}")
    wall = time.time() - t0
    try:
        score, note = evaluate(env, task)
    except Exception:
        score, note = 0.0, "evaluator crashed:\n" + traceback.format_exc()
    return {
        "id": task["id"], "instruction": task["instruction"],
        "finished": res["finished"], "answer_text": res.get("answer_text", ""),
        "score": score, "eval_note": note, "wall_s": round(wall, 1),
    }


def load_tasks(category=None, task_id=None):
    cats = [category] if category else sorted(os.listdir(WAA_TASKS))
    tasks = []
    for cat in cats:
        cat_dir = os.path.join(WAA_TASKS, cat)
        if not os.path.isdir(cat_dir):
            continue
        for fn in sorted(os.listdir(cat_dir)):
            if not fn.endswith(".json"):
                continue
            t = json.loads(open(os.path.join(cat_dir, fn)).read().replace(
                "C:\\\\Users\\\\Docker", f"C:\\\\Users\\\\{WIN_USER}"))
            t["_category"] = cat
            if task_id and t["id"] != task_id:
                continue
            tasks.append(t)
    return tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", nargs="?", const="", default=None,
                    help="list categories, or tasks in a category")
    ap.add_argument("--category")
    ap.add_argument("--task-id")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--no-reset", action="store_true", help="skip the clean-desktop VM revert")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()

    if args.list is not None:
        if not args.list:
            for cat in sorted(os.listdir(WAA_TASKS)):
                n = len([f for f in os.listdir(os.path.join(WAA_TASKS, cat)) if f.endswith(".json")])
                print(f"{cat:25s} {n}")
        else:
            for t in load_tasks(category=args.list):
                print(f"{t['id']}  {t['instruction'][:90]}")
        return

    tasks = load_tasks(category=args.category, task_id=args.task_id)
    if not tasks:
        raise SystemExit(f"no tasks match category={args.category!r} task_id={args.task_id!r}")
    # Infeasible tasks expect an explicit FAIL action our loop doesn't have -- skip loudly.
    skipped = [t for t in tasks if t["evaluator"].get("func") == "infeasible"]
    tasks = [t for t in tasks if t["evaluator"].get("func") != "infeasible"]
    for t in skipped:
        print(f"[skip] {t['id']} (infeasible-type task; no FAIL action in our loop)")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    batch = time.strftime("waa_%Y%m%d_%H%M%S")
    results = []
    try:
        for t in tasks:
            print(f"\n=== [{t['_category']}] {t['id']}\n{t['instruction']}")
            r = run_task(t, max_steps=args.max_steps,
                         reset=not args.no_reset, record=not args.no_record)
            r["category"] = t["_category"]
            print(f"--> score={r['score']} finished={r['finished']} ({r['wall_s']}s) {r['eval_note']}")
            results.append(r)
    finally:
        import agent_loop_holo as loop
        loop.shutdown()
        out = os.path.join(RESULTS_DIR, f"{batch}.json")
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        n_pass = sum(1 for r in results if r["score"] >= 1.0)
        print(f"\n{n_pass}/{len(results)} passed -> {out}")


if __name__ == "__main__":
    main()
