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
    parse_response,
    SYSTEM_PROMPT,
    TOOLS,
)
from agent_loop_holo import _frame_changed

from tools.probe_holo_structured_output import (
    STRUCTURED_RESPONSE_SCHEMA,
    STRUCTURED_SYSTEM_PROMPT,
    project_tool_call,
)

DEFAULT_RUN_DIR = "/home/aaron/workspace/vllm/runs/waa__28b91a24-5d97-4c2a-891c-dccbd3820c62-WOS_20260719_074932"

# Cheapest possible fix, tested as its own arm: does a plain WORKED EXAMPLE in the system
# prompt text (no history tampering, no architecture change -- literally one paragraph
# appended to the existing SYSTEM_PROMPT) recover note uptake on REAL, unmodified null-note
# history? If yes, this is a one-line change to kvm_agent/models/holo.py's SYSTEM_PROMPT.
WORKED_EXAMPLE_ADDENDUM = (
    "\n\nExample of a good note: after clicking a menu that opened, a good note would be "
    "\"Calculator menu is open, showing calc modes: Standard, Scientific, Date calculation. "
    "Need to click Date calculation.\" -- specific, references what's on screen now, and "
    "records exactly what you'd need if this screenshot were about to disappear."
)


def _tools_with_required_note():
    """A different, structurally-cheap fix hypothesis: instead of seeding history
    precedent, mark `note` REQUIRED (not optional) in each tool's JSON schema. If the
    model must satisfy a hard schema constraint (unlike an instruction it can ignore),
    real accumulated null-note history may not matter -- test this on the SAME real,
    unmodified history as arm (a)/(a'')."""
    import copy
    tools = copy.deepcopy(TOOLS)
    for t in tools:
        params = t["function"]["parameters"]
        if "note" in params["properties"] and "note" not in params["required"]:
            params["required"].append("note")
    return tools


def call_fc_custom_tools(tools, instruction, image_data_url, image_w, image_h,
                          target="local", history=None, temperature=0.8, enable_thinking=True,
                          max_history_images=1, notes=None):
    """Like call_holo_full, but with an overridable tools list -- used to test
    note-as-required without touching kvm_agent/models/holo.py."""
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(observation_message(image_data_url, instruction, notes=notes))
    trim_to_last_n_images(messages, n=max_history_images)
    resp = client.chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="required",
        max_tokens=4096, temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    message = resp.choices[0].message.model_dump()
    action = parse_response(message, image_w, image_h)
    usage = resp.usage.model_dump() if resp.usage else {}
    return action, message, usage


def call_fc_custom_prompt(system_prompt, instruction, image_data_url, image_w, image_h,
                           target="local", history=None, temperature=0.8, enable_thinking=True,
                           max_history_images=1, notes=None):
    """Like kvm_agent.models.holo.call_holo_full, but with an overridable system prompt --
    used to test a modified SYSTEM_PROMPT (worked example addendum) without needing to
    monkeypatch the module."""
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append(observation_message(image_data_url, instruction, notes=notes))
    trim_to_last_n_images(messages, n=max_history_images)
    resp = client.chat.completions.create(
        model=model, messages=messages, tools=TOOLS, tool_choice="required",
        max_tokens=4096, temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    message = resp.choices[0].message.model_dump()
    action = parse_response(message, image_w, image_h)
    usage = resp.usage.model_dump() if resp.usage else {}
    return action, message, usage


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


def build_fc_history(meta, steps, depth, max_history_images, seed_notes=False):
    """Exactly mirrors agent_loop_holo.py's run() history construction.

    seed_notes: see build_structured_history's docstring -- same discriminator, applied to
    the actual production (function-calling) history shape via _seed_note_into_tool_calls.
    If uptake recovers here, the fix ships as a tiny change to the EXISTING loop (seed the
    `notes` list / add a one-shot example), no architecture change needed."""
    instruction = meta["goal"]
    history = []
    for i in range(depth):
        step = steps[i]
        data_url = png_bytes_to_data_url(step["png"])
        step_instruction = instruction if i == 0 else ""
        tool_calls = step["message"].get("tool_calls") or []
        if not tool_calls:
            continue
        if seed_notes:
            tool_calls = _seed_note_into_tool_calls(tool_calls, step)
        changed = _frame_changed(steps[i]["png"], steps[i + 1]["png"]) if i + 1 < len(steps) else False
        tool_content = f"Action executed. Screen {'changed.' if changed else 'did not visibly change.'}"
        history.append(observation_message(data_url, step_instruction))
        history.append({"role": "assistant", "content": step["message"].get("content") or "", "tool_calls": tool_calls})
        history.append({"role": "tool", "tool_call_id": tool_calls[0].get("id", "call_0"), "content": tool_content})
        trim_to_last_n_images(history, n=max_history_images)
    return history


def _seed_note_into_tool_calls(tool_calls, step):
    """Same discriminator as build_structured_history's seed_notes, applied to the
    PRODUCTION function-calling history shape: injects a synthetic note into the first
    tool call's `arguments` JSON (the exact leaf param real production code reads -- see
    kvm_agent/models/holo.py NOTE_PARAM), so a note-writing precedent appears in the
    model's own reconstructed history without changing anything about the mechanism."""
    import copy
    tool_calls = copy.deepcopy(tool_calls)
    call = tool_calls[0]
    try:
        args = json.loads(call["function"]["arguments"])
    except (json.JSONDecodeError, TypeError, KeyError):
        return tool_calls
    if not args.get("note"):
        synthetic = _synthetic_note(step)
        if synthetic:
            args["note"] = synthetic
            call["function"]["arguments"] = json.dumps(args)
    return tool_calls


def _synthetic_note(step):
    """Derive a plausible note the model MIGHT have written for this step, from its own
    captured reasoning_content -- used only for the seed_notes A/B (see build_structured_history).
    Not a real note the model chose to write; a stand-in to test whether SEEING note-writing
    precedent in history (content) rather than its mere absence (depth alone) changes uptake."""
    reasoning = (step["message"].get("reasoning_content") or "").strip()
    if not reasoning:
        return None
    # First sentence, or first ~140 chars if no clear sentence boundary -- short, like a
    # real state-summary note, not a full reasoning dump.
    first_sentence = reasoning.split(". ")[0].strip()
    return (first_sentence[:140] + ("..." if len(first_sentence) > 140 else "")) or None


def build_structured_history(meta, steps, depth, max_history_images, seed_notes=False):
    """Same turns, native-style shape: each past assistant turn is a JSON string
    {note, thought, tool_calls: [<one reconstructed tool_call>]}, no tool-result
    turn (structured mode's only memory channel back to the model is `note`,
    same as native).

    seed_notes (advisor-suggested discriminator, 2026-07-19): the real run's history has
    note=None on every turn because function-calling never populated it -- so a structured
    replay retrofit from that history ALSO shows note=None throughout, which may just be
    the model pattern-matching its own (reconstructed) no-notes precedent rather than
    anything about depth per se. seed_notes=True populates each past turn's note with a
    synthetic state-summary (see _synthetic_note) so the model sees note-writing precedent
    in its own history. If uptake recovers here, the fix is a note-seed/one-shot example on
    the EXISTING function-calling loop, not a structured-output migration -- test this
    before spending rig time on a live structured-output rollout."""
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
        if seed_notes and note is None:
            note = _synthetic_note(step)
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

    fc_notes, fc_seeded_notes, fc_worked_example_notes, fc_required_note, st_notes, st_seeded_notes = 0, 0, 0, 0, 0, 0
    for r in range(reps):
        fc_history = build_fc_history(meta, steps, depth, max_history_images, seed_notes=False)
        action, message, usage = call_holo_full("", current_data_url, w, h, target=target,
                                                  history=fc_history, max_history_images=max_history_images, notes=[])
        note = action.get("note")
        fc_notes += bool(note)
        print(f"  [rep {r}] (a) function-calling (null history):   note={note!r}  action={action.get('action')} "
              f"coord={action.get('coordinate')}")

        # (a'') cheapest possible fix: REAL unmodified null-note history (same as arm a),
        # but SYSTEM_PROMPT gets one worked-example paragraph appended. No history
        # tampering, no architecture change -- if this alone recovers uptake, it's a
        # one-line SYSTEM_PROMPT edit to ship.
        action, message, usage = call_fc_custom_prompt(
            SYSTEM_PROMPT + WORKED_EXAMPLE_ADDENDUM, "", current_data_url, w, h, target=target,
            history=fc_history, max_history_images=max_history_images, notes=[])
        note = action.get("note")
        fc_worked_example_notes += bool(note)
        print(f"  [rep {r}] (a'') function-calling (worked example): note={note!r}  action={action.get('action')} "
              f"coord={action.get('coordinate')}")

        # (a''') different cheap fix hypothesis: REAL unmodified null-note history (same
        # as arm a), but `note` is REQUIRED in the tool schema instead of optional -- a
        # hard constraint the model must satisfy, vs. an instruction/example it can ignore.
        action, message, usage = call_fc_custom_tools(
            _tools_with_required_note(), "", current_data_url, w, h, target=target,
            history=fc_history, max_history_images=max_history_images, notes=[])
        note = action.get("note")
        fc_required_note += bool(note)
        print(f"  [rep {r}] (a''') function-calling (note required): note={note!r}  action={action.get('action')} "
              f"coord={action.get('coordinate')}")

        # (a') the discriminator that actually matters for shipping: does seeding note
        # precedent into the EXISTING function-calling history (no architecture change)
        # recover uptake the same way it did for structured output above?
        fc_seeded_history = build_fc_history(meta, steps, depth, max_history_images, seed_notes=True)
        action, message, usage = call_holo_full("", current_data_url, w, h, target=target,
                                                  history=fc_seeded_history, max_history_images=max_history_images, notes=[])
        note = action.get("note")
        fc_seeded_notes += bool(note)
        print(f"  [rep {r}] (a') function-calling (seeded history): note={note!r}  action={action.get('action')} "
              f"coord={action.get('coordinate')}")

        st_history = build_structured_history(meta, steps, depth, max_history_images, seed_notes=False)
        st_history.append(observation_message(current_data_url, "", notes=[]))
        trim_to_last_n_images(st_history, n=max_history_images)
        try:
            result = call_structured_with_messages(st_history, target=target)
        except Exception as e:
            print(f"  [rep {r}] (b) structured (null history):  CALL FAILED {type(e).__name__}: {e}")
        else:
            parsed = result["parsed"]
            note = parsed.get("note")
            st_notes += bool(note)
            tc = parsed["tool_calls"][0]
            proj = project_tool_call(tc, w, h)
            print(f"  [rep {r}] (b) structured (null history):  note={note!r}  action={proj.get('action')} "
                  f"coord={proj.get('coordinate')}  thought={parsed.get('thought','')[:150]!r}")

        # (c) advisor-suggested discriminator: same structured schema, but past turns'
        # notes are seeded with a synthetic state-summary instead of None -- isolates
        # note-writing PRECEDENT (content) from mere history PRESENCE (depth).
        st_seeded_history = build_structured_history(meta, steps, depth, max_history_images, seed_notes=True)
        st_seeded_history.append(observation_message(current_data_url, "", notes=[]))
        trim_to_last_n_images(st_seeded_history, n=max_history_images)
        try:
            result = call_structured_with_messages(st_seeded_history, target=target)
        except Exception as e:
            print(f"  [rep {r}] (c) structured (seeded history): CALL FAILED {type(e).__name__}: {e}")
            continue
        parsed = result["parsed"]
        note = parsed.get("note")
        st_seeded_notes += bool(note)
        tc = parsed["tool_calls"][0]
        proj = project_tool_call(tc, w, h)
        print(f"  [rep {r}] (c) structured (seeded history): note={note!r}  action={proj.get('action')} "
              f"coord={proj.get('coordinate')}  thought={parsed.get('thought','')[:150]!r}")

    print(f"  ---> depth {depth}: fc/null {fc_notes}/{reps}, fc/worked-example {fc_worked_example_notes}/{reps}, "
          f"fc/required {fc_required_note}/{reps}, fc/seeded {fc_seeded_notes}/{reps}, "
          f"structured/null {st_notes}/{reps}, structured/seeded {st_seeded_notes}/{reps}")
    return fc_notes, fc_worked_example_notes, fc_required_note, fc_seeded_notes, st_notes, st_seeded_notes


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
    for depth, (fc, fc_worked, fc_required, fc_seeded, st, st_seeded) in results.items():
        print(f"  depth {depth:3d}: fc/null {fc}/{args.reps}   fc/worked-example {fc_worked}/{args.reps}   "
              f"fc/required {fc_required}/{args.reps}   fc/seeded {fc_seeded}/{args.reps}   "
              f"structured/null {st}/{args.reps}   structured/seeded {st_seeded}/{args.reps}")


if __name__ == "__main__":
    main()
