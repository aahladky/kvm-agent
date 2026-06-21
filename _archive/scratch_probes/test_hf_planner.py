"""
test_hf_planner.py — prove the planner contract works against a Hugging Face endpoint.

Steps:
  1. resolve HF token (huggingface_hub.get_token() or env)
  2. PING  — tiny chat completion (connectivity + auth + model available?)
  3. TEXT PLAN — decompose a goal with NO image (tests JSON + step schema)
  4. VISION PLAN — decompose with a screenshot (tests the image path; needs a VLM)

Config via env (all optional):
  HF_PLANNER_MODEL  (default Qwen/Qwen2.5-VL-7B-Instruct)
  HF_PLANNER_BASE   (default https://router.huggingface.co/v1)
  HF_TOKEN / HUGGINGFACE_TOKEN / HUGGINGFACEHUB_API_TOKEN  (or a prior `huggingface-cli login`)
Run:  python test_hf_planner.py
"""
import sys, os, json
sys.path.insert(0, r"C:\Dev\vllm")
from planner import HFPlanner, _extract_json, SYSTEM  # noqa

MODEL = os.environ.get("HF_PLANNER_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct").strip()
BASE = os.environ.get("HF_PLANNER_BASE", "https://router.huggingface.co/v1").strip()
SHOT = os.environ.get("HF_PLANNER_SHOT", r"C:\Dev\vllm\_dbg\probe_frame.png").strip()

print("=== HF planner test ===")
print("base :", BASE)
print("model:", MODEL)
tok = HFPlanner._hf_token()
print("token:", "present" if tok else "MISSING")
if not tok:
    print("\nNo HF token found. Set HF_TOKEN (or run `huggingface-cli login`) and re-run.")
    sys.exit(0)

import openai
client = openai.OpenAI(base_url=BASE, api_key=tok)

# 1) PING
print("\n[1] PING ...")
try:
    r = client.chat.completions.create(model=MODEL, max_tokens=10,
        messages=[{"role": "user", "content": "Reply with exactly: OK"}])
    print("    ->", repr((r.choices[0].message.content or "")[:60]))
except Exception as e:
    print("    PING FAILED:", repr(e)[:400])
    print("    (model may not be served by your providers / endpoint — try another model via HF_PLANNER_MODEL)")
    sys.exit(1)

# 2) TEXT PLAN (no image)
print("\n[2] TEXT PLAN ...")
goal = "Open Notepad and type: hello world. Then open Calculator and compute 6 * 7."
try:
    plan = HFPlanner(model=MODEL, base_url=BASE, send_image=False).decompose(goal)
    print(f"    valid JSON plan, {len(plan)} steps:")
    print(json.dumps(plan, indent=1))
except Exception as e:
    print("    TEXT PLAN FAILED:", repr(e)[:400])

# 3) VISION PLAN (image) — needs a VLM
print("\n[3] VISION PLAN ...")
if not os.path.exists(SHOT):
    print("    (no screenshot at", SHOT, "- skipping)")
else:
    try:
        png = open(SHOT, "rb").read()
        plan2 = HFPlanner(model=MODEL, base_url=BASE, send_image=True).decompose(
            "Look at the current screen and write a short plan to fill the Profit column "
            "(Selling Price minus Cost Price) for the visible data table.", png)
        print(f"    valid JSON plan from screenshot, {len(plan2)} steps:")
        print(json.dumps(plan2, indent=1)[:1800])
    except Exception as e:
        print("    VISION PLAN FAILED:", repr(e)[:400])
        print("    (model may be text-only, or the endpoint doesn't accept OpenAI image_url)")

print("\n=== done ===")
