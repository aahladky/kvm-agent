"""Offline A/B probe: native-style JSON-schema-constrained structured output
(note+thought+tool_calls as mandatory top-level keys) vs. our current OpenAI
function-calling (note as an optional leaf param on whichever tool got picked).

WHY THIS EXISTS (2026-07-19): a real side-by-side of native holo-desktop-cli vs.
our ported-prompt harness on the IDENTICAL windows_calc date-difference task
(native: captured in a prior session's scratchpad as native_calc_summary.txt;
ours: runs/waa__28b91a24-5d97-4c2a-891c-dccbd3820c62-WOS_20260719_074932)
showed our `note` param at 0/40 uptake and our loop-detection only partially
working (same dead coordinate re-clicked 5+ times while the model's own
reasoning_content repeats near-verbatim "currently showing 2010-2019... click
the left arrow again"). Native hit the identical stuck-widget class but
recovered in 2 attempts. The diagnosis: native's `note`/`thought` are REQUIRED
top-level keys in a JSON-schema-constrained response -- the model cannot skip
engaging with them, even to set note=null -- while ours are an optional leaf
arg buried inside one tool call, invisible unless the model spontaneously adds
it. This is a MECHANISM gap, not a wording gap (see kvm_agent/models/holo.py's
SYSTEM_PROMPT header comment for what was already tried).

This script does NOT touch agent_loop_holo.py's real parsing path. It is a
standalone, offline (no rig/Pico/WAA) comparison: same test image, same
instruction, called two ways against the same live holo3.1 endpoint --
(a) our existing function-calling path (kvm_agent.models.holo.call_holo_full)
(b) a new response_format=json_schema path mirroring native's
    {note, thought, tool_calls} shape, single tool_call (maxItems=1 --
    NOT adopting native's multi-call batching, that's a separate, larger
    decision the 2026-07-19 session doc explicitly deferred).

Test image default: a real captured frame of the exact widget that broke both
runs (Calculator's year-picker decade grid, "2020 - 2029" with 2026 selected,
up/down carets top-right at roughly (323,185)/(372,185) -- NOT a left-edge
arrow, which is what our run spent 6+ dead clicks searching for at x=72).

Usage:
    python tools/probe_holo_structured_output.py
    python tools/probe_holo_structured_output.py --image path/to/frame.png \
        --instruction "..." --target local
"""
from __future__ import annotations

import argparse
import json

from kvm_agent.llm.ollama import openai_client
from kvm_agent.models.holo import (
    _target_config,
    image_path_to_data_url,
    call_holo_full,
    project_point,
    _scalar,
)

DEFAULT_IMAGE = "/home/aaron/.claude/jobs/7bb54671/tmp/calc_year_view.png"
DEFAULT_INSTRUCTION = (
    "Calculate how many years, months, weeks and days are between 10/08/1980 "
    "(MM/DD/YYYY) and 8/2/2024 using the calculator app, and save the result in "
    "a file called 'Differences.txt' on the Desktop. You are currently setting "
    "the From date and have opened the year picker, which shows the decade "
    "'2020 - 2029' with 2026 selected. You need to navigate back to 1980."
)

# Mirrors native's per-tool object shape (tool_name const + fields) but reuses
# OUR existing action vocabulary (kvm_agent/models/holo.py TOOLS), not
# native's richer one (no double_click/move_to/key_down/key_up/update_plan/
# load_skill here -- those are a separate adoption question, out of scope for
# this note/thought mechanism probe).
TOOL_CALL_SCHEMAS = [
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "element", "x", "y"],
        "properties": {
            "tool_name": {"const": "click"},
            "element": {"type": "string", "description": "Detailed description of the target UI element"},
            "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "content"],
        "properties": {
            "tool_name": {"const": "write"},
            "content": {"type": "string", "description": "Content to write"},
            "press_enter": {"type": "boolean", "default": False},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "key"],
        "properties": {
            "tool_name": {"const": "press_key"},
            "key": {"type": "string", "description": "Key name or '+'-joined combo, e.g. 'ctrl+a'"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "direction", "x", "y"],
        "properties": {
            "tool_name": {"const": "scroll"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "x1", "y1", "x2", "y2"],
        "properties": {
            "tool_name": {"const": "drag_and_drop"},
            "x1": {"type": "integer"},
            "y1": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "content"],
        "properties": {
            "tool_name": {"const": "answer"},
            "content": {"type": "string", "description": "The answer content"},
        },
    },
]

STRUCTURED_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "step",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["note", "thought", "tool_calls"],
            "properties": {
                "note": {
                    "type": ["string", "null"],
                    "description": (
                        "Persist task-relevant information from THIS observation before it's "
                        "gone from memory (only the last screenshot is kept): values, coordinates "
                        "of controls you found, dialog text, file paths, what you already tried and "
                        "whether it worked. Notes build on previous notes -- don't restate old info. "
                        "null if nothing new is worth persisting."
                    ),
                },
                "thought": {
                    "type": "string",
                    "description": (
                        "Reason about the next step. Explicitly assess: did your last action "
                        "succeed? Have you tried this exact action before -- if it already failed, "
                        "you MUST pivot (different coordinate, different control, a keyboard "
                        "shortcut, or Escape and re-enter the flow). What is the single best next "
                        "tool call?"
                    ),
                },
                "tool_calls": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {"anyOf": TOOL_CALL_SCHEMAS},
                },
            },
        },
    },
}

STRUCTURED_SYSTEM_PROMPT = (
    "You are a GUI agent. You control the computer shown in screenshots. "
    "Given a screenshot and an instruction, decide the next action. "
    "Coordinates x and y must be integers in [0, 1000], normalized to the screenshot dimensions. "
    "You must always emit `note`, `thought`, and exactly one `tool_calls` entry -- `note` and "
    "`thought` are mandatory fields even when note has nothing new (use null). "
    "If the screenshot already shows the instruction accomplished, call the `answer` tool "
    "immediately with a brief confirmation instead of taking another action."
)


def call_structured(instruction: str, image_data_url: str, target: str = "local",
                     temperature: float = 0.8, enable_thinking: bool = True) -> dict:
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = [
        {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        },
    ]
    resp = client.chat.completions.create(
        model=model, messages=messages,
        response_format=STRUCTURED_RESPONSE_SCHEMA,
        max_tokens=4096, temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    message = resp.choices[0].message
    raw = message.content
    parsed = json.loads(raw)
    usage = resp.usage.model_dump() if resp.usage else {}
    return {"parsed": parsed, "raw": raw, "reasoning_content": getattr(message, "reasoning_content", None), "usage": usage}


def project_tool_call(tc: dict, image_w: int, image_h: int) -> dict:
    """Same [0,1000]->pixel projection as kvm_agent.models.holo.parse_response, so the
    printed coordinates are directly comparable to a function-calling run's output."""
    name = tc.get("tool_name")
    if name == "click":
        return {"action": "left_click", "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h), "element": tc.get("element")}
    if name == "scroll":
        return {"action": "scroll", "direction": tc.get("direction"), "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h)}
    if name == "drag_and_drop":
        return {"action": "drag", "start": project_point(_scalar(tc["x1"]), _scalar(tc["y1"]), image_w, image_h),
                "coordinate": project_point(_scalar(tc["x2"]), _scalar(tc["y2"]), image_w, image_h)}
    if name == "write":
        return {"action": "type", "text": tc.get("content", ""), "press_enter": tc.get("press_enter", False)}
    if name == "press_key":
        return {"action": "key", "key": tc.get("key")}
    if name == "answer":
        return {"action": "finished", "text": tc.get("content", "")}
    return {"action": "error", "reason": "unknown_tool_name", "raw": tc}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    ap.add_argument("--target", default="local", choices=["local", "hosted"])
    ap.add_argument("--reps", type=int, default=1, help="repeat both calls N times (temperature=0.8 is stochastic)")
    args = ap.parse_args()

    from PIL import Image
    with Image.open(args.image) as im:
        image_w, image_h = im.size
    image_data_url = image_path_to_data_url(args.image)

    print(f"image: {args.image} ({image_w}x{image_h})")
    print(f"instruction: {args.instruction}\n")

    for rep in range(args.reps):
        print(f"{'='*70}\nREP {rep}\n{'='*70}")

        print("\n--- (a) function-calling (current production path) ---")
        action, message, usage = call_holo_full(args.instruction, image_data_url, image_w, image_h, target=args.target)
        print("note:", repr(action.get("note")))
        print("action:", action)
        print("reasoning_content:", repr((message.get("reasoning_content") or "")[:400]))
        print("usage:", usage)

        print("\n--- (b) structured output (note+thought+tool_calls mandatory) ---")
        try:
            result = call_structured(args.instruction, image_data_url, target=args.target)
        except Exception as e:
            print(f"STRUCTURED OUTPUT CALL FAILED: {type(e).__name__}: {e}")
            print("(server may not support response_format=json_schema with nested anyOf/const -- see docstring)")
            continue
        parsed = result["parsed"]
        print("note:", repr(parsed.get("note")))
        print("thought:", repr(parsed.get("thought")))
        tc = parsed["tool_calls"][0]
        print("tool_call (raw):", tc)
        print("tool_call (projected):", project_tool_call(tc, image_w, image_h))
        print("reasoning_content:", repr((result.get("reasoning_content") or "")[:400]))
        print("usage:", result["usage"])


if __name__ == "__main__":
    main()
