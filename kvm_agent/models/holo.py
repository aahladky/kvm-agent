"""Holo3.1 adapter: builds the request, calls the endpoint, parses the response into a
normalized action dict, and projects coordinates to screen pixels.

Ported from the software-layer bring-up (validated GO, see
docs/FINDINGS_holo_bringup.md / docs/FORMAT_NOTES_holo.md — 100% local grounding rate
on the Phase-4 harness, coordinate formula confirmed at 1920x1080 and 3132x1515). This
port only changes how the endpoint is reached (kvm_agent.llm.ollama.openai_client +
kvm_agent.config.CFG, matching this repo's "never hardcode an endpoint" convention) —
the validated parsing/projection logic (TOOLS, SYSTEM_PROMPT, project_point,
parse_response, DroppedActionCounter) is unchanged from the bring-up copy.

Normalized action shapes (mirrors the prior EvoCUA-era agent loop's action dict, so an
eventual loop is a drop-in):
    {"action": "left_click", "coordinate": [x, y], "element": "..."}
    {"action": "type", "text": "...", "press_enter": bool}
    {"action": "scroll", "direction": "up"|"down"|"left"|"right"}
    {"action": "drag", "start": [x1, y1], "coordinate": [x2, y2]}
    {"action": "finished", "text": "..."}

Format contract (verified empirically -- see docs/FORMAT_NOTES_holo.md, not assumed
from vendor docs):
    - Native function-calling: message.tool_calls, one call per step, content always "".
    - message.arguments is a JSON-encoded STRING, must be json.loads()'d.
    - Coordinates are integers in [0, 1000], normalized to the exact image sent;
      projection is raw / 1000 * image_dimension.

STATUS (Phase I0 of HOLO_INTEGRATION_PLAN.md): this is the validated software-layer
adapter, re-pointed at CFG. It has NOT been re-exercised against the live rig / capture
resolution from this repo -- that's Phase I3 (re-verify the [0,1000] projection at the
actual capture resolution) and Phase I4 (first live call_holo() through agent_loop_holo.py).
"""
from __future__ import annotations

import json
import logging

from kvm_agent.config import CFG
from kvm_agent.llm.ollama import openai_client

logger = logging.getLogger("holo")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click at a point on the screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Description of the target UI element"},
                    "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
                    "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
                },
                "required": ["element", "x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Type text, optionally pressing Enter afterward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the screen in a direction.",
            "parameters": {
                "type": "object",
                "properties": {"direction": {"type": "string", "enum": ["up", "down", "left", "right"]}},
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag_and_drop",
            "description": "Drag from one point to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer"},
                    "y1": {"type": "integer"},
                    "x2": {"type": "integer"},
                    "y2": {"type": "integer"},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer",
            "description": "Give a final text answer / mark the task complete.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a GUI agent. You control the computer shown in screenshots. "
    "Given a screenshot and an instruction, call exactly one tool to perform the next action. "
    "Coordinates x and y must be integers in [0, 1000], normalized to the screenshot dimensions."
)


class DroppedActionCounter:
    """Loud, from-day-one counter for non-terminal steps that parsed to zero actions.

    Guardrail from the bring-up plan: "If a non-terminal step parses to zero actions,
    log it loudly with a counter -- never silently no-op." A prior format bug (EvoCUA
    era) hid for 3 sessions precisely because a strict parser dropped valid output
    silently -- see CLAUDE.md's "make failure loud" discipline.
    """

    def __init__(self):
        self.count = 0

    def bump(self, reason: str, raw_message: dict):
        self.count += 1
        logger.warning("DROPPED ACTION #%d (%s): raw message=%s", self.count, reason, raw_message)


dropped_actions = DroppedActionCounter()


def project_point(raw_x: float, raw_y: float, image_w: int, image_h: int) -> list[float]:
    """[0,1000]-normalized -> screen pixels. See docs/FORMAT_NOTES_holo.md for the
    empirical verification (0-17.5px error at every corner/edge of a 1920x1080 calibration
    image). Re-verify at the live capture resolution before trusting it (Phase I3)."""
    return [raw_x / 1000 * image_w, raw_y / 1000 * image_h]


def _scalar(v):
    """Coerce a coordinate value to a scalar.

    Observed on the hosted reference API (never locally, across 80 local reps in the
    bring-up's ground_probe.py): x/y sometimes come back as a [min, max] range list
    instead of a scalar, despite the tool schema declaring "type": "integer". Take the
    midpoint rather than crash or silently drop -- see docs/FORMAT_NOTES_holo.md
    "hosted vs local" section. Local (llama.cpp/B70) has never shown this.
    """
    if isinstance(v, list):
        logger.warning("non-scalar coordinate value %r, using midpoint", v)
        return sum(v) / len(v)
    return v


def build_messages(instruction: str, image_data_url: str, history: list[dict] | None = None) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    )
    return messages


def parse_response(message: dict, image_w: int, image_h: int) -> dict:
    """Normalize one assistant message (native function-calling) into an action dict.

    Never returns None silently -- an unparseable or empty response becomes
    {"action": "error", ...} and bumps dropped_actions, per the loud-failure guardrail.
    """
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        dropped_actions.bump("no tool_calls in message", message)
        return {"action": "error", "reason": "no_tool_calls", "raw": message}

    call = tool_calls[0]
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"]["arguments"])
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        dropped_actions.bump(f"unparseable arguments: {e}", message)
        return {"action": "error", "reason": "bad_arguments_json", "raw": message}

    if name == "click":
        if "x" not in args or "y" not in args:
            dropped_actions.bump("click missing x/y", message)
            return {"action": "error", "reason": "click_missing_xy", "raw": message}
        return {
            "action": "left_click",
            "coordinate": project_point(_scalar(args["x"]), _scalar(args["y"]), image_w, image_h),
            "element": args.get("element"),
        }
    if name == "write":
        return {"action": "type", "text": args.get("content", ""), "press_enter": args.get("press_enter", False)}
    if name == "scroll":
        return {"action": "scroll", "direction": args.get("direction")}
    if name == "drag_and_drop":
        return {
            "action": "drag",
            "start": project_point(_scalar(args["x1"]), _scalar(args["y1"]), image_w, image_h),
            "coordinate": project_point(_scalar(args["x2"]), _scalar(args["y2"]), image_w, image_h),
        }
    if name == "answer":
        return {"action": "finished", "text": args.get("content", "")}

    dropped_actions.bump(f"unknown tool name {name!r}", message)
    return {"action": "error", "reason": "unknown_tool", "tool_name": name, "raw": message}


def _target_config(target: str):
    if target == "local":
        return CFG.holo_local_url, CFG.holo_model, None
    if target == "hosted":
        return CFG.holo_hosted_url, CFG.holo_hosted_model, (CFG.hai_api_key or None)
    raise ValueError(f"unknown target {target!r} (expected 'local' or 'hosted')")


def call_holo(instruction: str, image_data_url: str, image_w: int, image_h: int,
              target: str = "local", history: list[dict] | None = None) -> dict:
    """End-to-end: build request, call the endpoint, parse into a normalized action dict.

    Takes an already-encoded data URL + known dimensions (not a file path) so a live
    capture frame (kvm_agent.hardware.env.Camera.png_bytes()) can be passed straight
    through without a round-trip to disk.
    """
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = build_messages(instruction, image_data_url, history=history)
    resp = client.chat.completions.create(
        model=model, messages=messages, tools=TOOLS, max_tokens=600, temperature=0.0,
    )
    message = resp.choices[0].message.model_dump()
    return parse_response(message, image_w, image_h)


def image_path_to_data_url(path: str) -> str:
    import base64
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/png;base64,{b64}"


def png_bytes_to_data_url(png_bytes: bytes) -> str:
    import base64
    return f"data:image/png;base64,{base64.b64encode(png_bytes).decode()}"


def call_holo_image_path(instruction: str, image_path: str, target: str = "local",
                          history: list[dict] | None = None) -> dict:
    """Convenience wrapper matching the bring-up harness's file-based call signature
    (used by the offline self-test / probe scripts)."""
    from PIL import Image
    with Image.open(image_path) as im:
        w, h = im.size
    return call_holo(instruction, image_path_to_data_url(image_path), w, h, target=target, history=history)


def _self_test():
    """Offline self-test: re-parses the Phase-2 captured raw examples, no network."""
    import os
    fixture = os.path.join(os.path.dirname(__file__), "_fixtures", "holo_phase2_native_tools_raw.json")
    with open(fixture) as f:
        examples = json.load(f)

    image_w, image_h = 3132, 1515  # test_screens/llamaswap_ui.png, all Phase-2 examples used it
    ok = 0
    for ex in examples:
        action = parse_response(ex["message"], image_w, image_h)
        assert action["action"] != "error", f"failed to parse: {ex['instruction']!r} -> {action}"
        print(f"OK  {ex['instruction']!r:70s} -> {action}")
        ok += 1

    assert dropped_actions.count == 0, f"self-test should not drop any captured example, got {dropped_actions.count}"
    print(f"\n{ok}/{len(examples)} examples parsed cleanly. dropped_actions={dropped_actions.count}")

    # Spot-check the click projection against the bring-up's verified formula.
    click_ex = next(e for e in examples if e["message"]["tool_calls"][0]["function"]["name"] == "click")
    action = parse_response(click_ex["message"], image_w, image_h)
    raw_args = json.loads(click_ex["message"]["tool_calls"][0]["function"]["arguments"])
    expected = [raw_args["x"] / 1000 * image_w, raw_args["y"] / 1000 * image_h]
    assert action["coordinate"] == expected, (action["coordinate"], expected)
    print(f"Coordinate projection check OK: raw=({raw_args['x']},{raw_args['y']}) -> {action['coordinate']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
