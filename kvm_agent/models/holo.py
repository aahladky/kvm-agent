"""Holo3.1 adapter: builds the request, calls the endpoint, parses the response into a
normalized action dict, and projects coordinates to screen pixels.

Ported from the software-layer bring-up (validated GO, see
docs/FINDINGS_holo_bringup.md / docs/FORMAT_NOTES_holo.md — 100% local grounding rate
on the Phase-4 harness, coordinate formula confirmed at 1920x1080 and 3132x1515). The
bring-up's own tool schema/message format was never checked against H Company's actual
published convention (HOLO_TESTING_PLAN.md explicitly scoped that out: "No full agent
loop / multi-step task execution yet"). After Phase I5's first live multi-step run
surfaced the model never calling `answer`, a follow-up research pass fetched
hub.hcompany.ai/agent-loop and /element-localization directly and diffed this file
against them. Result: the [0,1000]->pixel formula was an exact match (nothing to fix);
everything else below was brought into line with the documented convention:
    - TOOLS: click/write/answer descriptions now match vendor wording verbatim
      (scroll/drag_and_drop are our own extensions -- not in any official example).
    - Every screenshot-bearing user turn is wrapped in <observation>...</observation>
      (observation_message()), per the documented chat-layout table.
    - tool_choice="required" is now set explicitly (docs: the documented cause of "tool
      calls come back as plain text" is exactly this being left unset).
    - temperature=0.8 + enable_thinking=True are now the defaults, matching the
      documented AGENT-LOOP config -- temperature=0.0/no-thinking is what the docs
      specify for the separate, stateless, single-shot element-localization endpoint,
      a different tool for a different job; this file had been configured like that one.
    - trim_to_last_n_images() keeps only the last 3 screenshots in history (docs: "more
      degrades accuracy"), evicting older image chunks to "[screenshot evicted]" text.
    - agent_loop_holo.py's run() now threads real tool-result content (a frame-diff-based
      "screen changed / did not visibly change" signal) instead of a hardcoded "ok" --
      docs flag exactly this gap as the cause of loops/forgetting.

Normalized action shapes (mirrors the prior EvoCUA-era agent loop's action dict, so an
eventual loop is a drop-in):
    {"action": "left_click", "coordinate": [x, y], "element": "..."}
    {"action": "type", "text": "...", "press_enter": bool}
    {"action": "scroll", "direction": "up"|"down"|"left"|"right"}
    {"action": "drag", "start": [x1, y1], "coordinate": [x2, y2]}
    {"action": "finished", "text": "..."}

Format contract (verified empirically -- see docs/FORMAT_NOTES_holo.md, not assumed
from vendor docs):
    - Native function-calling: message.tool_calls, one call per step. content was always
      "" in the bring-up's limited probe (no thinking, no multi-step) -- now that
      thinking + tool_choice=required are on for real multi-step runs this may no longer
      hold in general, so don't assume it; message.get("content") is preserved as-is.
    - message.arguments is a JSON-encoded STRING, must be json.loads()'d.
    - Coordinates are integers in [0, 1000], normalized to the exact image sent;
      projection is raw / 1000 * image_dimension.

STATUS: Phases I0-I5 of HOLO_INTEGRATION_PLAN.md are done and verified live (VM target,
SPICE-fullscreen capture, Pico HID over WiFi). This vendor-alignment pass landed after I5.
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
            # Descriptions below are verbatim from hub.hcompany.ai/agent-loop (click) and
            # the structured-output WriteArgs/AnswerArgs docstrings on the same page (the
            # function-calling example only spells out click+answer in full; write's
            # wording is ported across from structured-output mode, same underlying tool).
            "name": "click",
            "description": "Click at (x, y) coordinates",
            "parameters": {
                "type": "object",
                "properties": {
                    "element": {"type": "string", "description": "Detailed description of the target UI element"},
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
            "description": "Type text into the currently focused element without clicking first",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to write"},
                    "press_enter": {"type": "boolean", "default": False, "description": "Whether to press Enter after typing"},
                },
                "required": ["content"],
            },
        },
    },
    {
        # scroll/drag_and_drop: our own extensions, not in any official H Company example
        # (only click/write/answer are documented) -- unverified against vendor training,
        # kept since the bring-up's Phase-2 capture showed the model uses both correctly
        # when offered (see docs/FORMAT_NOTES_holo.md).
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll in a direction at a specific point on the screen. Move the cursor over the pane you want to scroll (x, y), then the wheel turns there.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "x": {"type": "integer", "description": "X coordinate of the point to scroll at, in [0, 1000]"},
                    "y": {"type": "integer", "description": "Y coordinate of the point to scroll at, in [0, 1000]"},
                },
                "required": ["direction", "x", "y"],
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
            "description": "Provide a final answer",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "The answer content"}},
                "required": ["content"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a GUI agent. You control the computer shown in screenshots. "
    "Given a screenshot and an instruction, call exactly one tool to perform the next action. "
    "Coordinates x and y must be integers in [0, 1000], normalized to the screenshot dimensions. "
    "After each action you will be shown an updated screenshot. Look at it before deciding your "
    "next action -- if it already shows the instruction accomplished, call the `answer` tool "
    "immediately with a brief confirmation instead of taking another action. Do not repeat an "
    "action that has already succeeded."
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


def observation_message(image_data_url: str, text: str = "") -> dict:
    """One <observation> user turn, per hub.hcompany.ai/agent-loop's documented
    function-calling chat layout: every screenshot-bearing user turn is wrapped in
    <observation>...</observation>. `text` is normally the task instruction on the first
    turn of a run and empty on later turns (the model already has it via history)."""
    prefix = f"<observation>\n{text}\n" if text else "<observation>\n"
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix},
            {"type": "image_url", "image_url": {"url": image_data_url}},
            {"type": "text", "text": "\n</observation>"},
        ],
    }


def build_messages(instruction: str, image_data_url: str, history: list[dict] | None = None) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(observation_message(image_data_url, instruction))
    return messages


def trim_to_last_n_images(messages: list[dict], n: int = 3) -> None:
    """In place: keep only the last n image_url chunks across all messages, replacing
    older ones with a "[screenshot evicted]" text chunk (docs: "Keep at most the last 3
    screenshots in context; more degrades accuracy") -- the <observation> text wrapper
    around each stays, only the image chunk itself gets replaced."""
    image_positions = [
        (mi, ci)
        for mi, m in enumerate(messages)
        for ci, chunk in enumerate(m.get("content") or [])
        if isinstance(m.get("content"), list) and chunk.get("type") == "image_url"
    ]
    for mi, ci in image_positions[:-n] if n > 0 else image_positions:
        messages[mi]["content"][ci] = {"type": "text", "text": "[screenshot evicted]"}


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
        out = {"action": "scroll", "direction": args.get("direction")}
        # Targeted scroll (flaw #10 fix, 2026-07-18): the wheel turns wherever the cursor
        # happens to sit, so an untargeted scroll silently scrolls the wrong pane -- or
        # nothing at all (11 zero-effect scrolls in the scroll_to_about battery task).
        if "x" in args and "y" in args:
            out["coordinate"] = project_point(_scalar(args["x"]), _scalar(args["y"]), image_w, image_h)
        return out
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


def call_holo_full(instruction: str, image_data_url: str, image_w: int, image_h: int,
                    target: str = "local", history: list[dict] | None = None,
                    temperature: float = 0.8, enable_thinking: bool = True,
                    max_history_images: int = 3) -> tuple[dict, dict, dict]:
    """Like call_holo, but also returns the raw assistant message (dict, via model_dump())
    and token usage -- a multi-step loop needs the message to thread real history (the
    assistant tool-call + a tool-result message per step; see agent_loop_holo.py's run())
    and usage for run instrumentation (see kvm_agent/instrumentation/run_log.py). call_holo()
    itself discards both since the REPL/single-shot callers only need the parsed action.

    Defaults (temperature=0.8, enable_thinking=True, tool_choice="required") match
    hub.hcompany.ai/agent-loop's documented agent-loop config, NOT the separate
    element-localization endpoint's (temperature=0.0, no thinking) -- those are for a
    stateless single-shot grounding primitive, a different use case from this multi-step
    loop. "Reasoning is essential in agent mode (Holo was trained to plan before each
    step), so leave it on" -- past reasoning is dropped between turns by the chat
    template regardless (only the parsed tool call gets re-added to history, never
    reasoning_content), so this only affects the CURRENT step's decision quality, not
    what's threaded forward.

    LATENCY NOTE (2026-07-17, measured against this rig, not yet acted on -- a Phase I6
    candidate if per-step latency needs tuning): image FORMAT doesn't matter locally --
    PNG vs JPEG at the same resolution produced byte-identical prompt_tokens (2842 both),
    since the vision encoder sees decoded pixels either way and loopback transfer of a
    few MB is sub-millisecond regardless. Image RESOLUTION does matter -- 960x540 (1/4 the
    pixels of 1920x1080) used ~35% fewer prompt_tokens (1834 vs 2842). But completion_tokens
    (the reasoning trace, generated token-by-token -- sequential, slow) varied 90-154
    across otherwise-identical calls, and one untimed call ran 30.5s for reasons unrelated
    to image size -- reasoning-length variance at temperature=0.8 looks like a bigger
    latency lever than image size/format. If tuning latency: downscaling resolution is a
    real (if modest) win with a grounding-accuracy tradeoff to weigh; re-encoding as JPEG
    is not worth doing.
    """
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = build_messages(instruction, image_data_url, history=history)
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


def call_holo(instruction: str, image_data_url: str, image_w: int, image_h: int,
              target: str = "local", history: list[dict] | None = None,
              temperature: float = 0.8, enable_thinking: bool = True) -> dict:
    """End-to-end: build request, call the endpoint, parse into a normalized action dict.

    Takes an already-encoded data URL + known dimensions (not a file path) so a live
    capture frame (kvm_agent.hardware.env.Camera.png_bytes()) can be passed straight
    through without a round-trip to disk.
    """
    action, _, _ = call_holo_full(instruction, image_data_url, image_w, image_h, target=target, history=history,
                                   temperature=temperature, enable_thinking=enable_thinking)
    return action


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
