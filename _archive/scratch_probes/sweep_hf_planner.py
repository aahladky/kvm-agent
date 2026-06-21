"""
sweep_hf_planner.py — planner quality vs model size, via the HF router.
Same two prompts for every model:
  TEXT : "Open Notepad and type: hello world. Then open Calculator and compute 6 * 7."
  VISION: practice-sheet screenshot + "fill the Profit column (Selling - Cost) for the table"
Prints each model's plan + latency; saves full JSON. Per-model try/except so one failure
doesn't abort the sweep.
"""
import sys, os, json, time
sys.path.insert(0, r"C:\Dev\vllm")
from planner import HFPlanner

BASE = "https://router.huggingface.co/v1"
SHOT = r"C:\Dev\vllm\_dbg\probe_frame.png"
MODELS = [
    "Qwen/Qwen3-VL-8B-Instruct",
    "Qwen/Qwen3-VL-30B-A3B-Instruct",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen3-VL-235B-A22B-Instruct",
    "google/gemma-3-27b-it",
]
TEXT_GOAL = "Open Notepad and type: hello world. Then open Calculator and compute 6 * 7."
VIS_GOAL = ("Look at the current screen. Write a plan to fill the Profit column of the data "
            "table (Profit = Selling Price minus Cost Price) for every data row.")
png = open(SHOT, "rb").read() if os.path.exists(SHOT) else None
results = {}

for m in MODELS:
    print("\n" + "=" * 70 + f"\nMODEL: {m}")
    rec = {}
    # text plan
    try:
        t = time.time()
        plan = HFPlanner(model=m, base_url=BASE, send_image=False).decompose(TEXT_GOAL)
        rec["text"] = {"secs": round(time.time() - t, 1), "steps": len(plan), "plan": plan}
        print(f"  TEXT  {rec['text']['secs']}s  {len(plan)} steps: "
              + " | ".join(s.get("op", "?") + (":" + str(s.get("app") or s.get("text") or s.get("number==") or s.get("key") or s.get("combo") or "")) for s in plan))
    except Exception as e:
        rec["text"] = {"error": repr(e)[:200]}; print("  TEXT  ERROR:", repr(e)[:160])
    # vision plan
    if png:
        try:
            t = time.time()
            plan = HFPlanner(model=m, base_url=BASE, send_image=True).decompose(VIS_GOAL, png)
            rec["vision"] = {"secs": round(time.time() - t, 1), "steps": len(plan), "plan": plan}
            print(f"  VISION {rec['vision']['secs']}s  {len(plan)} steps:")
            for s in plan:
                print("     ", json.dumps(s))
        except Exception as e:
            rec["vision"] = {"error": repr(e)[:200]}; print("  VISION ERROR:", repr(e)[:160])
    results[m] = rec

os.makedirs(r"C:\Dev\vllm\runs", exist_ok=True)
out = r"C:\Dev\vllm\runs\hf_sweep_" + time.strftime("%Y%m%d_%H%M%S") + ".json"
json.dump(results, open(out, "w"), indent=2)
print("\nsaved", out)
