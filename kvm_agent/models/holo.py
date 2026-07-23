"""Holo3.1 adapter: builds the request, calls the endpoint, parses the response into
normalized action dicts, and projects coordinates to screen pixels.

VERBATIM-NATIVE REWRITE (2026-07-21, feature/native-verbatim): matches H Company's own
holo-desktop-cli runtime (hai-agent-runtime v0.1.9) as closely as the KVM operating model
allows. Ground truth was extracted from the sha256-pinned runtime binary itself (the CLI's
open-source repo is only an installer; the agent loop ships closed-source): the system
prompt template and runtime config are preserved verbatim in docs/native/ (the 2026-07-19
proxy capture of these was lost with /tmp -- never rely on a capture again, re-extract).

What "verbatim" covers here:
    - SYSTEM_PROMPT is rendered from native's actual Jinja template
      (docs/native/local-desktop-2026-06-12.j2), not a condensation. The template's
      conditional blocks are evaluated with OUR tool set, exactly as native's own renderer
      would (update_plan present, load_skill/localizer/search absent).
    - The <output_format> schema block is embedded in the system prompt, per
      hub.hcompany.ai/agent-loop: "the model was trained with the schema visible in both
      the prompt and structured_outputs, and dropping either copy noticeably hurts
      reliability." (The superseded branch dropped it; this restores it.)
    - RESPONSE_SCHEMA is native's shape: one object per step, {note, thought, tool_calls},
      note NULLABLE ("Set to None if nothing new" -- the required-non-null workaround from
      the superseded branch is retired; re-probe uptake under the verbatim prompt before
      concluding it's still needed), tool_calls is native's BATCHED array (minItems=1, no
      max) -- native executes the calls sequentially on one desktop and returns only the
      batch's final screenshot (see the prompt's own BATCHING GUIDELINES).
    - Tool set and field names match native's desktop config (10 of its 13 tools --
      see deviations below): click_desktop, double_click_desktop, write_desktop,
      scroll_desktop (scroll_size in wheel clicks), drag_to_desktop (from CURRENT cursor
      position, not a specified start), move_to_desktop, hotkey_desktop (keys LIST +
      repeat_count -- NOT our old '+'-joined press_key string), hold_and_tap_key_desktop,
      update_plan, answer. Descriptions are byte-verbatim from the runtime's constants.
    - Tool results go back as user messages wrapped in <tool_output tool="...">, the
      channel native's chat mapper actually uses (confirmed in the runtime binary's
      constants; the 2026-07-19 capture's claim that "native has no execution-result
      feedback channel" was WRONG).
    - Request params match native's config (docs/native/*.yaml): temperature=0.8,
      reasoning_effort="medium", max_completion_tokens=2048, thinking on.
    - Model input is a JPEG at native resolution (native transcodes screenshots to JPEG,
      clamped at 1920w, sends full-res). 720p downscale remains available as the A/B knob
      (CFG.holo_model_input_res) pending the resolution/timing test.
    - max_history_images default is native's max_images: 3.

DISCLOSED DEVIATIONS (each forced or explicitly judged, none silent):
    1. key_down_desktop / key_up_desktop OMITTED: our HID bridge has no key-hold primitive
       (only tap/combo) -- offering a tool we can't execute would be worse than omitting it.
    2. write_desktop's third bool ("Whether to clear existing text before typing") OMITTED:
       its description was recovered from the runtime but its FIELD NAME could not be
       byte-verified, and a guessed name is worse than a missing optional field. The same
       effect is native-idiomatic via batching: hotkey ctrl+a then write.
    3. load_skill OMITTED: native's skills catalog (per-app SKILL.md hints) is not wired
       into this harness. The prompt's Skills block is therefore not rendered (native's
       own conditional).
    4. <tool_output> PAYLOAD is ours, not native's (native's result text isn't in the
       binary's recoverable constants): each executed call reports our camera-based
       frame-diff signal ("screen changed / did not visibly change", with tile-max
       magnitude and rough region -- the 2026-07-21 follow-up: report WHAT changed, not a
       bare binary). The channel shape is verbatim; the content is our instrumentation.
    5. update_plan's effect is minimal: the plan is recorded and ACKed in its tool_output;
       native's display_plan rendering is not ported.
    6. wait_after_s=0.3 (native's fixed per-tool settle) is replaced by our camera-based
       settle -- better suited to a KVM pipeline where the observation channel has real
       latency. Model-contract-neutral.
    7. Assistant history re-adds the model's parsed JSON content only, never
       reasoning_content (native's own rule: reasoning is dropped between turns).
    8. The old <notes> block is GONE: native needs no separate notes channel because
       assistant turns (with their note fields) persist in history; only images are
       evicted by trim. Our trim behaves the same way.

Normalized action shapes (a LIST per step now -- native batches):
    {"action": "left_click", "coordinate": [x, y], "element": "..."}
    {"action": "double_click", "coordinate": [x, y], "element": "..."}
    {"action": "type", "text": "...", "press_enter": bool}
    {"action": "hotkey", "keys": ["ctrl", "a"], "repeat_count": int}
    {"action": "hold_and_tap", "hold_keys": [...], "tap_keys": [...]}
    {"action": "move_to", "coordinate": [x, y]}
    {"action": "scroll", "direction": "up"|"down"|"left"|"right", "scroll_size": int, "coordinate": [x, y]}
    {"action": "drag_to", "coordinate": [x, y]}      # from CURRENT cursor position
    {"action": "update_plan", "goals": [{"title": ..., "status": ...}]}
    {"action": "finished", "text": "..."}
parse_response returns {"actions": [...], "note": str|None, "thought": str|None}.

Format contract (structured output, response_format=json_schema):
    - message.content is a JSON-encoded STRING matching RESPONSE_SCHEMA, json.loads() it.
    - message.reasoning_content holds the thinking trace; never re-add it to history.
    - Coordinates are integers in [0, 1000], normalized to the exact image sent;
      projection is raw / 1000 * screen_dimension (the model-input image is a uniform
      downscale of the same screen content, so the projection basis is the real screen).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from kvm_agent.config import CFG
from kvm_agent.llm.ollama import openai_client
from kvm_agent.models.base import StepDecision

logger = logging.getLogger("holo")

REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_PROMPT_PATH = REPO_ROOT / "docs" / "native" / "local-desktop-2026-06-12.j2"


class _RequestLog:
    """Append-only JSONL log of every actual request/response to the model (ported from
    the superseded branch 2026-07-19; before it, NOTHING captured the outgoing request, so
    whether the system prompt/schema were correctly constructed for a given step could not
    be verified after the fact). image_url data URIs are redacted to a byte count -- the
    actual pixels are separately available via RunRecorder's saved step_NN.png."""

    def __init__(self, path=None):
        self.path = path or os.path.join(CFG.logs_dir, "holo_requests.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.lock = threading.Lock()

    @staticmethod
    def _redact(messages):
        out = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                content = [
                    ({**c, "image_url": {"url": f"<redacted, {len(c['image_url']['url'])} chars>"}}
                     if c.get("type") == "image_url" else c)
                    for c in content
                ]
                out.append({**m, "content": content})
            else:
                out.append(m)
        return out

    def write(self, record):
        record["ts"] = time.time()
        line = json.dumps(record)
        with self.lock:
            with open(self.path, "a") as f:
                f.write(line + "\n")
                f.flush()


REQUEST_LOG = _RequestLog()

# Tool schemas, native's tool_name-const-discriminator shape. Tool and field descriptions
# are byte-verbatim from the hai-agent-runtime v0.1.9 binary's recovered constants (click/
# write/answer additionally match hub.hcompany.ai/agent-loop's published example). The
# tools native's desktop config enables but we omit (key_down/key_up, load_skill) and
# why -- see module docstring.
TOOL_CALL_SCHEMAS = [
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Click at (x, y) coordinates",
        "required": ["tool_name", "element", "x", "y"],
        "properties": {
            "tool_name": {"const": "click_desktop"},
            "element": {"type": "string", "description": "Detailed description of the target UI element to click on"},
            "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Double click at (x, y) coordinates",
        "required": ["tool_name", "element", "x", "y"],
        "properties": {
            "tool_name": {"const": "double_click_desktop"},
            "element": {"type": "string", "description": "Detailed description of the target UI element to double click on"},
            "x": {"type": "integer", "description": "The x coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "The y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Type text into the currently focused element without clicking first",
        "required": ["tool_name", "content"],
        "properties": {
            "tool_name": {"const": "write_desktop"},
            "content": {"type": "string", "description": "Content to write"},
            "press_enter": {"type": "boolean", "default": False, "description": "Whether to press Enter after typing"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Scroll in a given direction, placing the cursor at (x, y) first",
        "required": ["tool_name", "direction", "scroll_size", "x", "y"],
        "properties": {
            "tool_name": {"const": "scroll_desktop"},
            "element": {"type": "string", "description": "Detailed description of the target UI element to scroll on"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"],
                          "description": "Direction to scroll in"},
            "scroll_size": {"type": "integer",
                            "description": "Scroll size in mouse wheel clicks (at most 100). Choose it carefully considering the OS and the objective of the scroll"},
            "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Drag from current position to specific coordinates",
        "required": ["tool_name", "x", "y"],
        "properties": {
            "tool_name": {"const": "drag_to_desktop"},
            "element": {"type": "string", "description": "Detailed description of the target UI element to drag to"},
            "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Move mouse to (x, y) coordinates",
        "required": ["tool_name", "x", "y"],
        "properties": {
            "tool_name": {"const": "move_to_desktop"},
            "element": {"type": "string", "description": "Detailed description of the target UI element to move the mouse to"},
            "x": {"type": "integer", "description": "X coordinate as integer in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate as integer in [0, 1000]"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Press multiple keys simultaneously (max 5). Adapt the keys depending on the operating system",
        "required": ["tool_name", "keys"],
        "properties": {
            "tool_name": {"const": "hotkey_desktop"},
            "keys": {"type": "array", "minItems": 1, "maxItems": 5,
                     "items": {"type": "string"},
                     "description": "List of 1 to 5 key(s) to press simultaneously: e.g. ['ctrl', 'alt', 't'] for Ubuntu, ['cmd', 't'] for MacOS..."},
            "repeat_count": {"type": "integer", "default": 1,
                             "description": "Number of times to repeat the hotkey press"},
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Hold a list of key(s) and tap a list of key(s) in sequence. Adapt the keys depending on the operating system.",
        "required": ["tool_name", "hold_keys", "tap_keys"],
        "properties": {
            "tool_name": {"const": "hold_and_tap_key_desktop"},
            "hold_keys": {"type": "array", "minItems": 1, "maxItems": 3,
                          "items": {"type": "string"},
                          "description": "List of 1 to 3 key(s) to hold down"},
            "tap_keys": {"type": "array", "minItems": 1, "maxItems": 5,
                         "items": {"type": "string"},
                         "description": "List of 1 to 5 key(s) to tap in sequence"},
        },
    },
    {
        # update_plan: native's planning tool (sagent.lib.tools.planning.UpdatePlan).
        # Description verbatim from the runtime. Goal fields: title/status, descriptions
        # verbatim. Execution is harness-side bookkeeping (see agent_loop_holo.py).
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Create and manage your task plan with hierarchical goals. Always provide the "
            "complete list of goals every time you call this tool. When creating an initial "
            "plan, include all goals with the first one as 'running' and others as 'todo'. "
            "When marking progress, include all goals with updated statuses (mark completed "
            "as 'done', set next as 'running'). When replanning after blockers, include your "
            "done/failed goals plus new goals. Maintain only one goal with status 'running' "
            "at any given time."
        ),
        "required": ["tool_name", "updates"],
        "properties": {
            "tool_name": {"const": "update_plan"},
            "updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "status"],
                    "properties": {
                        "title": {"type": "string",
                                  "description": "Clear, actionable goal description (start with verb)"},
                        "status": {"type": "string", "enum": ["todo", "running", "done", "failed"],
                                   "description": "Current status (todo/running/done/failed)"},
                    },
                },
            },
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "description": "Provide a final answer",
        "required": ["tool_name", "content"],
        "properties": {
            "tool_name": {"const": "answer"},
            "content": {"type": "string", "description": "The answer content"},
        },
    },
]

# Top-level response schema, native's shape: {note, thought, tool_calls} all required,
# note NULLABLE (native: "Set to None if nothing new"), tool_calls a BATCHED array
# (minItems=1, no maxItems -- native batches multiple calls per step).
RESPONSE_SCHEMA = {
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
                        "Task-relevant information from the previous observation. Persist "
                        "values, short text excerpts, file paths, dialog messages, "
                        "confirmations, button/field states -- anything future steps need "
                        "that can't be re-read once the screen changes. Notes build on "
                        "previous notes; never restate old info. Set to null if nothing new."
                    ),
                },
                "thought": {
                    "type": "string",
                    "description": "Reasoning about next steps",
                },
                "tool_calls": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"anyOf": TOOL_CALL_SCHEMAS},
                },
            },
        },
    },
}


def _render_native_prompt(max_steps: int = 200, max_time_s: int = 3600) -> str:
    """Render native's actual system-prompt Jinja template (docs/native/...j2) with OUR
    tool set, exactly as native's renderer would: update_plan present (so the Planning
    block renders), load_skill/skills/search/localizer absent (their blocks drop out).
    Then append the <output_format> schema block -- hub.hcompany.ai/agent-loop: the model
    was trained with the schema visible in BOTH the prompt and structured_outputs, and
    dropping either copy noticeably hurts reliability."""
    import jinja2
    from datetime import datetime
    template = jinja2.Template(NATIVE_PROMPT_PATH.read_text())
    tools = [s["properties"]["tool_name"]["const"] for s in TOOL_CALL_SCHEMAS]
    rendered = template.render(
        services={},            # no 'localizer' service -> the coordinate (element-string)
                                # variant of the Element Localization block, matching our setup
        tools=tools,            # drives the update_plan / search conditionals
        skill_hints="",         # no skills catalog -> Skills block drops (native conditional)
        system_timestamp=lambda _t: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        start_time=time.time(),
        max_steps=max_steps,
        max_time_s=max_time_s,
    )
    schema_json = json.dumps(RESPONSE_SCHEMA["json_schema"]["schema"], indent=2)
    return rendered + f"\n\n<output_format>\n```json\n{schema_json}\n```\n</output_format>"


SYSTEM_PROMPT = _render_native_prompt()


class DroppedActionCounter:
    """Loud, from-day-one counter for steps that parsed to zero actions.

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
    image). image_w/h here is the PROJECTION BASIS (the real screen the HID moves on),
    not the model-input image -- the model input is a uniform downscale of the same
    content, so the normalized coordinates transfer."""
    return [raw_x / 1000 * image_w, raw_y / 1000 * image_h]


def _scalar(v):
    """Coerce a coordinate value to a scalar.

    Observed on the hosted reference API (never locally, across 80 local reps in the
    bring-up's ground_probe.py): x/y sometimes come back as a [min, max] range list
    instead of a scalar, despite the schema declaring "type": "integer". Take the
    midpoint rather than crash or silently drop -- see docs/FORMAT_NOTES_holo.md
    "hosted vs local" section. Local (llama.cpp/B70) has never shown this.

    Shape-guarded (2026-07-21 review P1-10): anything OTHER than a scalar, a
    single-element list, or a 2-element range raises ValueError -- a zero-length
    list was a ZeroDivisionError and a longer nonsense list became a
    plausible-looking midpoint click. Loud is correct here: run()'s model-call
    guard records the step as dropped instead of clicking an invented coordinate.
    """
    if isinstance(v, list):
        if len(v) == 1:
            logger.warning("single-element coordinate list %r, unwrapping", v)
            return v[0]
        if len(v) == 2:
            logger.warning("range coordinate value %r, using midpoint", v)
            return (v[0] + v[1]) / 2
        raise ValueError(
            f"nonsense coordinate list (expected scalar or [min, max] range): {v!r}")
    return v


def observation_message(image_data_url: str, text: str = "") -> dict:
    """One <observation> user turn: every screenshot-bearing user turn is wrapped in
    <observation>...</observation> (native chat layout, confirmed in the runtime binary's
    constants). `text` is normally the task instruction on the first turn of a run and
    empty on later turns (the model already has it via history)."""
    parts = [text] if text else []
    prefix = "<observation>\n" + "\n".join(parts) + "\n" if parts else "<observation>\n"
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix},
            {"type": "image_url", "image_url": {"url": image_data_url}},
            {"type": "text", "text": "\n</observation>"},
        ],
    }


def tool_output_message(tool_name: str, result: str) -> dict:
    """One <tool_output> user message -- native's execution-result channel (confirmed in
    the runtime binary's chat-mapper constants: '<tool_output tool=\"' ...). Sent as a
    USER message, not a synthetic tool role -- hub.hcompany.ai/agent-loop's pitfall table
    flags exactly that mistake ("Tool result has no effect")."""
    return {
        "role": "user",
        "content": f'<tool_output tool="{tool_name}">\n{result}\n</tool_output>',
    }


def build_messages(instruction: str, image_data_url: str, history: list[dict] | None = None) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(observation_message(image_data_url, instruction))
    return messages


def trim_to_last_n_images(messages: list[dict], n: int = 3) -> None:
    """In place: keep only the last n image_url chunks across all messages, replacing
    older ones with a "[screenshot evicted]" text chunk. Native keeps max_images: 3
    (docs/native/*.yaml); more degrades accuracy per hub.hcompany.ai/agent-loop. The
    <observation> text wrapper around each evicted image stays -- only the image chunk
    itself is replaced. Assistant turns (content is a plain string in structured-output
    mode) are untouched; notes/thoughts in them therefore persist for the whole run,
    which is exactly how native's note mechanism works (no separate notes channel)."""
    image_positions = [
        (mi, ci)
        for mi, m in enumerate(messages)
        for ci, chunk in enumerate(m.get("content") or [])
        if isinstance(m.get("content"), list) and chunk.get("type") == "image_url"
    ]
    for mi, ci in image_positions[:-n] if n > 0 else image_positions:
        messages[mi]["content"][ci] = {"type": "text", "text": "[screenshot evicted]"}


def _error_step(reason: str, message: dict, **extra) -> dict:
    return {"actions": [], "note": None, "thought": None, "error": reason, "raw": message, **extra}


def parse_response(message: dict, image_w: int, image_h: int) -> dict:
    """Normalize one assistant message (structured output) into a step dict:
    {"actions": [...], "note": str|None, "thought": str|None}. `actions` is a LIST --
    native batches multiple tool calls per step; execution order is array order.

    Never returns None silently -- an unparseable or empty response becomes
    {"actions": [], "error": ...} and bumps dropped_actions, per the loud-failure
    guardrail. message["content"] is the JSON string the schema constrains the model to
    emit; there is no message.tool_calls in structured-output mode.
    """
    raw_content = message.get("content") or ""
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as e:
        dropped_actions.bump(f"unparseable content JSON: {e}", message)
        return _error_step("bad_content_json", message)

    note = parsed.get("note")
    note = note.strip() or None if isinstance(note, str) else None
    thought = parsed.get("thought")

    tool_calls = parsed.get("tool_calls") or []
    if not tool_calls:
        dropped_actions.bump("no tool_calls in parsed content", message)
        return _error_step("no_tool_calls", message, thought=thought)

    actions = []
    for tc in tool_calls:
        name = tc.get("tool_name")

        if name in ("click_desktop", "double_click_desktop"):
            if "x" not in tc or "y" not in tc:
                dropped_actions.bump(f"{name} missing x/y", message)
                return _error_step(f"{name}_missing_xy", message, note=note, thought=thought)
            actions.append({
                "action": "left_click" if name == "click_desktop" else "double_click",
                "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h),
                "element": tc.get("element"),
            })
        elif name == "write_desktop":
            actions.append({"action": "type", "text": tc.get("content", ""),
                            "press_enter": tc.get("press_enter", False)})
        elif name == "scroll_desktop":
            actions.append({
                "action": "scroll",
                "direction": tc.get("direction"),
                "scroll_size": tc.get("scroll_size", 3),
                "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h)
                              if "x" in tc and "y" in tc else None,
                "element": tc.get("element"),
            })
        elif name == "drag_to_desktop":
            if "x" not in tc or "y" not in tc:
                dropped_actions.bump("drag_to_desktop missing x/y", message)
                return _error_step("drag_to_missing_xy", message, note=note, thought=thought)
            actions.append({
                "action": "drag_to",
                "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h),
                "element": tc.get("element"),
            })
        elif name == "move_to_desktop":
            if "x" not in tc or "y" not in tc:
                dropped_actions.bump("move_to_desktop missing x/y", message)
                return _error_step("move_to_missing_xy", message, note=note, thought=thought)
            actions.append({
                "action": "move_to",
                "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h),
                "element": tc.get("element"),
            })
        elif name == "hotkey_desktop":
            keys = tc.get("keys") or []
            if not keys:
                dropped_actions.bump("hotkey_desktop missing keys", message)
                return _error_step("hotkey_missing_keys", message, note=note, thought=thought)
            actions.append({"action": "hotkey", "keys": keys,
                            "repeat_count": int(tc.get("repeat_count") or 1)})
        elif name == "hold_and_tap_key_desktop":
            hold, tap = tc.get("hold_keys") or [], tc.get("tap_keys") or []
            if not hold or not tap:
                dropped_actions.bump("hold_and_tap_key_desktop missing hold_keys/tap_keys", message)
                return _error_step("hold_and_tap_missing_keys", message, note=note, thought=thought)
            actions.append({"action": "hold_and_tap", "hold_keys": hold, "tap_keys": tap})
        elif name == "update_plan":
            actions.append({"action": "update_plan", "goals": tc.get("updates") or []})
        elif name == "answer":
            actions.append({"action": "finished", "text": tc.get("content", "")})
        else:
            dropped_actions.bump(f"unknown tool_name {name!r}", message)
            return _error_step("unknown_tool", message, tool_name=name, note=note, thought=thought)

    return {"actions": actions, "note": note, "thought": thought}


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
    """One step: build request, call the endpoint, parse into a step dict; also returns
    the raw assistant message (dict, via model_dump()) and token usage.

    Params match native's config (docs/native/*.yaml): temperature=0.8,
    reasoning_effort="medium", max_completion_tokens=2048 (mapped to max_tokens here),
    thinking on. NOT the separate stateless element-localization endpoint's
    (temperature=0.0, no thinking) -- a different tool for a different job. Past reasoning
    is dropped between turns by the chat template regardless (only the parsed JSON content
    gets re-added to history, never reasoning_content), so thinking only affects the
    CURRENT step's decision quality.

    reasoning_effort is passed as a top-level request field (native sends it to H's
    gateway). llama.cpp may ignore it -- flagged, not relied on; enable_thinking via
    chat_template_kwargs is what actually controls the local reasoning trace.
    """
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = build_messages(instruction, image_data_url, history=history)
    trim_to_last_n_images(messages, n=max_history_images)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, response_format=RESPONSE_SCHEMA,
            max_tokens=2048, temperature=temperature,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": enable_thinking},
                "reasoning_effort": "medium",
            },
        )
    except Exception as e:
        REQUEST_LOG.write({"target": target, "model": model, "messages": REQUEST_LOG._redact(messages),
                           "response_format": RESPONSE_SCHEMA, "temperature": temperature,
                           "enable_thinking": enable_thinking, "error": str(e),
                           "http_ms": round((time.time() - t0) * 1000.0, 1)})
        raise
    message = resp.choices[0].message.model_dump()
    if getattr(resp.choices[0], "finish_reason", None) == "length":
        # A reasoning-truncated JSON parses as a format error otherwise, hiding the
        # real cause (second review #12).
        logger.warning("response hit the token cap (finish_reason='length') -- "
                       "JSON likely truncated; will surface as a dropped step")
    step = parse_response(message, image_w, image_h)
    usage = resp.usage.model_dump() if resp.usage else {}
    REQUEST_LOG.write({"target": target, "model": model, "messages": REQUEST_LOG._redact(messages),
                       "response_format": RESPONSE_SCHEMA, "temperature": temperature,
                       "enable_thinking": enable_thinking, "response_message": message,
                       "parsed_step": step, "usage": usage,
                       "http_ms": round((time.time() - t0) * 1000.0, 1)})
    return step, message, usage


def call_holo(instruction: str, image_data_url: str, image_w: int, image_h: int,
              target: str = "local", history: list[dict] | None = None,
              temperature: float = 0.8, enable_thinking: bool = True) -> dict:
    """End-to-end single step: build request, call, parse into a step dict.

    Takes an already-encoded data URL + known projection basis (not a file path) so a
    live capture frame can be passed straight through without a round-trip to disk.
    """
    step, _, _ = call_holo_full(instruction, image_data_url, image_w, image_h, target=target,
                                 history=history, temperature=temperature,
                                 enable_thinking=enable_thinking)
    return step


# Our normalized action kind -> native's tool_name vocabulary. Was inline in
# agent_loop_holo.run()'s per-action loop (roadmap Phase 1 seam, docs/ROADMAP.md Part 3
# Slice C): model-specific vocabulary belongs behind the ModelSession seam, not in the
# harness, which should only ever speak the normalized action kind.
ACTION_TO_TOOL_NAME = {
    "left_click": "click_desktop", "double_click": "double_click_desktop",
    "type": "write_desktop", "scroll": "scroll_desktop",
    "drag_to": "drag_to_desktop", "move_to": "move_to_desktop",
    "hotkey": "hotkey_desktop", "hold_and_tap": "hold_and_tap_key_desktop",
    "update_plan": "update_plan", "finished": "answer",
}


class HoloSession:
    """ModelSession (kvm_agent.models.base) for Holo3.1: owns conversation history and
    every native-shaped detail (observation/tool_output message construction, image
    trim, the tool-name map) behind decide()/commit(). agent_loop_holo.run() talks to
    this, never to build_messages/trim_to_last_n_images/tool_output_message directly.

    `call_fn` defaults to this module's own call_holo_full but is constructor-injected
    so a caller (agent_loop_holo.run()) can pass its OWN module-global reference --
    existing tests monkeypatch `agent_loop_holo.call_holo_full` and expect run() to
    pick up the patched function; since Python resolves a bare name at call time, run()
    passing its own `call_holo_full` global into HoloSession() each call preserves that
    without any test changes.
    """

    def __init__(self, target: str = "local", max_history_images: int = 3,
                 call_fn=None):
        self.target = target
        self.max_history_images = max_history_images
        self._call_fn = call_fn or call_holo_full
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history = []

    def decide(self, data_url: str, w: int, h: int, instruction: str) -> StepDecision:
        step, message, usage = self._call_fn(
            instruction, data_url, w, h, target=self.target, history=self.history,
            max_history_images=self.max_history_images)
        return StepDecision(step=step, message=message, usage=usage,
                            data_url=data_url, instruction=instruction)

    def tool_name(self, action_kind: str) -> str:
        return ACTION_TO_TOOL_NAME.get(action_kind, str(action_kind))

    def commit(self, decision: StepDecision, results: list[tuple[str, str]]) -> None:
        self.history.append(observation_message(decision.data_url, decision.instruction))
        self.history.append({"role": "assistant", "content": decision.message.get("content") or ""})
        for tool_name, text in results:
            self.history.append(tool_output_message(tool_name, text))
        trim_to_last_n_images(self.history, n=self.max_history_images)


def image_path_to_data_url(path: str) -> str:
    """JPEG data URL from a file -- native transcodes model-input screenshots to JPEG
    (screenshot_media_type: image/jpeg in docs/native/*.yaml). PNG inputs are decoded
    and re-encoded; quality 90 (native's exact quality isn't recoverable from the
    binary -- flagged, not guessed as gospel)."""
    import base64
    import io
    from PIL import Image
    with Image.open(path) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"


def jpeg_bytes_to_data_url(jpeg_bytes: bytes) -> str:
    import base64
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg_bytes).decode()}"


def call_holo_image_path(instruction: str, image_path: str, target: str = "local",
                          history: list[dict] | None = None) -> dict:
    """Convenience wrapper matching the bring-up harness's file-based call signature
    (used by the offline self-test / probe scripts)."""
    from PIL import Image
    with Image.open(image_path) as im:
        w, h = im.size
    return call_holo(instruction, image_path_to_data_url(image_path), w, h, target=target, history=history)


def _self_test():
    """Offline self-test: re-parses the captured structured-output fixtures, no network.
    Covers all 10 tools + a multi-call batch step."""
    fixture = os.path.join(os.path.dirname(__file__), "_fixtures", "holo_native_verbatim_raw.json")
    with open(fixture) as f:
        examples = json.load(f)

    ok = 0
    total_actions = 0
    for ex in examples:
        step = parse_response(ex["message"], ex["image_w"], ex["image_h"])
        assert not step.get("error"), f"failed to parse: {ex['instruction']!r} -> {step.get('error')}"
        assert step["actions"], f"no actions: {ex['instruction']!r}"
        print(f"OK  {ex['instruction']!r:70s} -> {len(step['actions'])} action(s), note={'yes' if step['note'] else 'null'}")
        ok += 1
        total_actions += len(step["actions"])

    assert dropped_actions.count == 0, f"self-test should not drop any captured example, got {dropped_actions.count}"
    print(f"\n{ok}/{len(examples)} examples parsed cleanly ({total_actions} actions). dropped_actions={dropped_actions.count}")

    # Spot-check the click projection against the verified formula.
    click_ex = next(e for e in examples
                    if json.loads(e["message"]["content"])["tool_calls"][0]["tool_name"] == "click_desktop")
    step = parse_response(click_ex["message"], click_ex["image_w"], click_ex["image_h"])
    raw_tc = json.loads(click_ex["message"]["content"])["tool_calls"][0]
    expected = [raw_tc["x"] / 1000 * click_ex["image_w"], raw_tc["y"] / 1000 * click_ex["image_h"]]
    assert step["actions"][0]["coordinate"] == expected, (step["actions"][0]["coordinate"], expected)
    print(f"Coordinate projection check OK: raw=({raw_tc['x']},{raw_tc['y']}) -> {step['actions'][0]['coordinate']}")

    # Every tool the schema offers must appear in the fixtures (parser coverage).
    schema_tools = {s["properties"]["tool_name"]["const"] for s in TOOL_CALL_SCHEMAS}
    fixture_tools = {tc["tool_name"] for e in examples
                     for tc in json.loads(e["message"]["content"])["tool_calls"]}
    missing = schema_tools - fixture_tools
    assert not missing, f"tools with no fixture coverage: {missing}"
    print(f"Tool coverage check OK: all {len(schema_tools)} tools exercised")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
