"""Does note-uptake collapse with REAL accumulated multi-turn history, for BOTH
function-calling and structured-output? A necessary follow-up to
probe_holo_structured_output.py: that script's single cold-start turn showed
note uptake at 4/6 (function-calling) and 2/6 (structured) -- i.e. BOTH
mechanisms use notes reasonably often with no history. But the real production
run this was diagnosing (runs/waa__28b91a24-5d97-4c2a-891c-dccbd3820c62-WOS_20260719_074932)
showed 0/40 note uptake. That gap (cold-start ~50-65% vs. in-run 0%) means the
single-shot probe was NOT actually testing the failure condition -- something
about being deep in a real multi-turn trajectory (accumulated tool-call/
tool-result history, or being in a stuck/repetitive state) suppresses note
usage far more than the schema-mechanism hypothesis alone predicts.

This script replays the REAL saved history from that failing run (step_00.png
.. step_KK.png + each step's actual raw model message, from which the true
[0,1000] tool-call args are recovered) to reconstruct the exact multi-turn
message list agent_loop_holo.py's run() would have built by step K, for BOTH
mechanisms:
  (a) function-calling history, exactly as run() builds it (observation/
      assistant-tool_calls/tool-result triples, trimmed to the last
      MAX_HISTORY_IMAGES screenshots) -- then one live call_holo_full() at
      depth K, matching what actually happened in the run bit-for-bit up to
      the live call.
  (b) an equivalent structured-output history -- same images/notes, each past
      assistant turn re-expressed as a {note, thought, tool_calls} JSON string
      (thought reconstructed from that step's saved reasoning_content) instead
      of an OpenAI tool_calls entry, no separate tool-result turn (native has
      none -- the note field IS the memory channel).

Does NOT touch agent_loop_holo.py or the rig. Pure replay against saved
run artifacts + one live call per mechanism per requested depth.

Usage:
    python tools/probe_holo_note_uptake_at_depth.py --depth 15
    python tools/probe_holo_note_uptake_at_depth.py --depths 5 10 15 20 --reps 2
"""
from __future__ import annotations

import argparse
import json
import os

from kvm_agent.config import CFG
from kvm_agent.llm.ollama import openai_client
from kvm_agent.models.holo import (
    _target_config,
    observation_message,
    trim_to_last_n_images,
    call_holo_full,
    png_bytes_to_data_url,
)
from agent_loop_holo import _frame_changed

from tools.probe_holo_structured_output import (
    STRUCTURED_RESPONSE_SCHEMA,
    STRUCTURED_SYSTEM_PROMPT,
    project_tool_call,
)

DEFAULT_RUN_DIR = "/home/aaron/workspace/vllm/runs/waa__28b91a24-5d97-4c2a-891c-dccbd3820c62-WOS_20260719_074932"


def load_run(run_dir: str):
    with open(os.path.join(run_dir, "meta.json")) as f:
        meta = json.load(f)
    steps = []
    i = 0
    while os.path.exists(os.path.join(run_dir, f"step_{i:02d}.json")):
        with open(os.path.join(run_dir, f"step_{i:02d}.json")) as f:
            step = json.load(f)
        with open(os.path.join(run_dir, f"step_{i:02d}.png"), "rb") as f:
            step["png"] = f.read()
        steps.append(step)
        i += 1
    return meta, steps


def build_fc_history(meta, steps, depth, max_history_images):
    """Exactly mirrors agent_loop_holo.py's run() history construction."""
    instruction = meta["goal"]
    history = []
    for i in range(depth):
        step = steps[i]
        data_url = png_bytes_to_data_url(step["png"])
        step_instruction = instruction if i == 0 else ""
        tool_calls = step["message"].get("tool_calls") or []
        if not tool_calls:
            continue
        changed = _frame_changed(steps[i]["png"], steps[i + 1]["png"]) if i + 1 < len(steps) else False
        tool_content = f"Action executed. Screen {'changed.' if changed else 'did not visibly change.'}"
        history.append(observation_message(data_url, step_instruction))
        history.append({"role": "assistant", "content": step["message"].get("content") or "", "tool_calls": tool_calls})
        history.append({"role": "tool", "tool_call_id": tool_calls[0].get("id", "call_0"), "content": tool_content})
        trim_to_last_n_images(history, n=max_history_images)
    return history


def build_structured_history(meta, steps, depth, max_history_images):
    """Same turns, native-style shape: each past assistant turn is a JSON string
    {note, thought, tool_calls: [<one reconstructed tool_call>]}, no tool-result
    turn (structured mode's only memory channel back to the model is `note`,
    same as native)."""
    instruction = meta["goal"]
    messages = []
    for i in range(depth):
        step = steps[i]
        data_url = png_bytes_to_data_url(step["png"])
        step_instruction = instruction if i == 0 else ""
        tool_calls = step["message"].get("tool_calls") or []
        if not tool_calls:
            continue
        call = tool_calls[0]
        try:
            raw_args = json.loads(call["function"]["arguments"])
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        note = raw_args.pop("note", None) or None
        tc = {"tool_name": call["function"]["name"], **raw_args}
        thought = (step["message"].get("reasoning_content") or "").strip()[:600] or "(no reasoning captured)"
        assistant_json = json.dumps({"note": note, "thought": thought, "tool_calls": [tc]})
        messages.append(observation_message(data_url, step_instruction))
        messages.append({"role": "assistant", "content": assistant_json})
        trim_to_last_n_images(messages, n=max_history_images)
    return messages


def call_structured_with_messages(messages, target="local", temperature=0.8, enable_thinking=True):
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    full_messages = [{"role": "system", "content": STRUCTURED_SYSTEM_PROMPT}] + messages
    resp = client.chat.completions.create(
        model=model, messages=full_messages,
        response_format=STRUCTURED_RESPONSE_SCHEMA,
        max_tokens=4096, temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    message = resp.choices[0].message
    parsed = json.loads(message.content)
    usage = resp.usage.model_dump() if resp.usage else {}
    return {"parsed": parsed, "reasoning_content": getattr(message, "reasoning_content", None), "usage": usage}


def run_at_depth(run_dir, depth, target, reps, max_history_images):
    meta, steps = load_run(run_dir)
    assert depth < len(steps), f"depth {depth} >= {len(steps)} steps available in this run"
    w, h = meta["screen_size"]
    current_png = steps[depth]["png"]
    current_data_url = png_bytes_to_data_url(current_png)
    real_action = steps[depth]["action"]

    print(f"\n{'#'*70}\nDEPTH {depth}  (real run's actual step {depth}: {real_action.get('action')} "
          f"note={real_action.get('note')!r})\n{'#'*70}")

    fc_notes, st_notes = 0, 0
    for r in range(reps):
        fc_history = build_fc_history(meta, steps, depth, max_history_images)
        action, message, usage = call_holo_full("", current_data_url, w, h, target=target,
                                                  history=fc_history, max_history_images=max_history_images, notes=[])
        note = action.get("note")
        fc_notes += bool(note)
        print(f"  [rep {r}] (a) function-calling: note={note!r}  action={action.get('action')} "
              f"coord={action.get('coordinate')}")

        st_history = build_structured_history(meta, steps, depth, max_history_images)
        st_history.append(observation_message(current_data_url, "", notes=[]))
        trim_to_last_n_images(st_history, n=max_history_images)
        try:
            result = call_structured_with_messages(st_history, target=target)
        except Exception as e:
            print(f"  [rep {r}] (b) structured: CALL FAILED {type(e).__name__}: {e}")
            continue
        parsed = result["parsed"]
        note = parsed.get("note")
        st_notes += bool(note)
        tc = parsed["tool_calls"][0]
        proj = project_tool_call(tc, w, h)
        print(f"  [rep {r}] (b) structured:      note={note!r}  action={proj.get('action')} "
              f"coord={proj.get('coordinate')}  thought={parsed.get('thought','')[:150]!r}")

    print(f"  ---> depth {depth}: function-calling note uptake {fc_notes}/{reps}, "
          f"structured note uptake {st_notes}/{reps}")
    return fc_notes, st_notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=DEFAULT_RUN_DIR)
    ap.add_argument("--depths", type=int, nargs="+", default=[15])
    ap.add_argument("--target", default="local", choices=["local", "hosted"])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-history-images", type=int, default=CFG.holo_history_images)
    args = ap.parse_args()

    results = {}
    for depth in args.depths:
        results[depth] = run_at_depth(args.run_dir, depth, args.target, args.reps, args.max_history_images)

    print(f"\n{'='*70}\nSUMMARY (reps={args.reps})\n{'='*70}")
    for depth, (fc, st) in results.items():
        print(f"  depth {depth:3d}: function-calling {fc}/{args.reps}   structured {st}/{args.reps}")


if __name__ == "__main__":
    main()
