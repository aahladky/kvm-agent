#!/usr/bin/env python3
"""
serving_probe.py — preflight for the model server, the way verify_hid is a preflight
for the HID channel.

The model server lives OUTSIDE this repo (llama-swap + modelctl,
`~/services/llama-swap/config.yaml`) and nothing here used to look at it. The parallel
with verify_hid is exact: the firmware's `online` flags can lie, so the camera
adjudicates; the serving config says what the server WOULD launch, so `/running` -- what
it IS running -- adjudicates.

    python tools/serving_probe.py             # probe, warm if cold, verdict
    python tools/serving_probe.py --no-warm   # observe only, never load a model
    python tools/serving_probe.py --json      # machine-readable, no artifact

FAIL-CLOSED, unlike `agent_loop_holo.boot()`'s serving check (which only warns): this
tool exits nonzero so it can gate a battery. It hard-fails on exactly three things,
each of which silently ruins a run rather than announcing itself:

  * endpoint unreachable            -- every model call will fail
  * model not configured            -- ditto, but looks like a model bug
  * resident model has NO mmproj    -- a VISION model that cannot see. It answers
                                       fluently from the text alone, so this reads as
                                       "the model got bad at grounding", which is the
                                       single most expensive misdiagnosis available
                                       here (AGENTS.md §2: the model is the last suspect)

Everything else -- context size, --image-min-tokens, KV cache quant, TTL, co-residency
-- is RECORDED, not asserted. Asserting a number nobody has calibrated manufactures
false alarms; recording it lets drift be noticed across runs, which is the actual goal.

Artifacts (AGENTS.md §1): runs/serving_probe_<ts>/probe.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.llm.serving import describe, serving_snapshot


def warm(model, timeout_s=600.0):
    """Smallest possible real completion, to force a load and time it. Returns
    (ok, seconds, detail). Uses the same client factory the loop does, so a failure
    here is a failure the loop would also have hit."""
    from kvm_agent.llm.ollama import openai_client
    base_url = CFG.holo_local_url
    t0 = time.time()
    try:
        client = openai_client(base_url=base_url, api_key="unused", timeout=timeout_s)
        client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "ok"}], max_tokens=1)
        return True, time.time() - t0, "ok"
    except Exception as e:                      # noqa: BLE001
        return False, time.time() - t0, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description="model-server preflight")
    ap.add_argument("--model", default=CFG.holo_model)
    ap.add_argument("--no-warm", action="store_true",
                    help="observe only; never trigger a model load")
    ap.add_argument("--json", action="store_true",
                    help="print JSON to stdout and write no runs/ artifact")
    args = ap.parse_args()

    report = {"model": args.model, "started": time.strftime("%Y%m%d_%H%M%S"),
              "client_model_input_res": CFG.holo_model_input_res,
              "client_history_images": CFG.holo_history_images}
    snap = serving_snapshot(model=args.model)
    report["before"] = snap
    if not args.json:
        print(f"[serving] {describe(snap)}")

    # A cold model is not a fault -- but the cold-load cost is worth knowing before a
    # battery attributes it to a slow first step.
    if snap.get("reachable") and snap.get("configured") and snap.get("resident") is False \
            and not args.no_warm:
        if not args.json:
            print(f"[serving] cold -- warming to measure the load cost...")
        ok, cold_s, detail = warm(args.model)
        report["cold_load_s"] = round(cold_s, 1)
        report["warm_ok"] = ok
        report["warm_detail"] = detail
        if ok:
            ok2, warm_s, _ = warm(args.model)
            report["warm_call_s"] = round(warm_s, 1)
            report["load_penalty_s"] = round(cold_s - warm_s, 1)
        snap = serving_snapshot(model=args.model)
        report["after"] = snap
        if not args.json:
            print(f"[serving] cold {report['cold_load_s']}s, warm "
                  f"{report.get('warm_call_s', '?')}s, penalty "
                  f"{report.get('load_penalty_s', '?')}s")
            print(f"[serving] {describe(snap)}")

    params = snap.get("params") or {}
    failures = []
    if not snap.get("reachable"):
        failures.append(f"endpoint {snap['endpoint']} unreachable: {snap.get('error')}")
    elif snap.get("configured") is False:
        failures.append(f"model {args.model!r} is not configured at {snap['endpoint']}")
    if snap.get("resident") and not params.get("has_mmproj", True):
        failures.append(f"resident {args.model!r} has NO mmproj -- a vision model that "
                        f"cannot see images; grounding would be blind but fluent")
    report["failures"] = failures
    report["ok"] = not failures

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        out_dir = os.path.join(CFG.runs_dir, f"serving_probe_{report['started']}")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "probe.json"), "w") as f:
            json.dump(report, f, indent=2, default=str)
        for key in ("ctx", "image_min_tokens", "cache_type_k", "cache_type_v",
                    "parallel", "quant", "split_mode", "tensor_split"):
            if params.get(key) is not None:
                print(f"[serving]   {key}: {params[key]}")
        if snap.get("co_resident"):
            print(f"[serving]   co-resident: {', '.join(str(m) for m in snap['co_resident'])}")
        for f_ in failures:
            print(f"[serving] FAIL: {f_}")
        print(f"[serving] {'OK' if report['ok'] else 'FAILED'} -> {out_dir}/probe.json")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
