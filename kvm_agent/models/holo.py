"""Holo3.1 adapter: builds the request, calls the endpoint, parses the response into a
normalized action dict, and projects coordinates to screen pixels.

REARCHITECTED 2026-07-19 (contamination audit follow-up): switched from OpenAI
function-calling to native holo-desktop-cli's ACTUAL mechanism -- JSON-schema-constrained
structured output, `response_format={"type":"json_schema",...}`, one object per step
containing {note, thought, tool_calls}. The prior function-calling adapter was our own
approximation of native's tool vocabulary, not native's actual request mechanism -- a
real, disclosed deviation from the reference implementation (see docs/SESSION_2026-07-19_
holo_focus_bug_and_native_prompt_port.md sec 5 for why function-calling was chosen
originally: "too large a change to make blind"). It's no longer blind: tools/probe_holo_
structured_output.py and tools/probe_holo_note_uptake_at_depth.py proved live, repeatedly,
that this exact local backend (llama.cpp/llama-swap serving holo3.1) supports
response_format=json_schema with the same nested anyOf/const discriminated-union shape
native's own captured request uses (see native_system_prompt_full.txt / native_request_
summary.txt, captured via a logging proxy 2026-07-19 -- not guessed).

DISCLOSED, DELIBERATE deviations from native's literal schema (contamination that's
understood and evidence-based, not accidental):
  1. `note` is REQUIRED and non-nullable (`"type": "string"`), not native's `["string",
     "null"]`. Native allows the model to skip persisting by returning null; empirically
     that risks a SELF-ANCHORING failure mode (tools/probe_holo_note_uptake_at_depth.py:
     structured mode with note nullable-but-required still showed 0/15 uptake on REAL
     accumulated null-history at every depth tested, collapsing the same way function-
     calling's optional note did) -- a single early null note can lock in silence for the
     rest of a run before any real precedent exists. Forcing non-empty (with "write a short
     note anyway if nothing new" in the field description) sidesteps that risk entirely
     rather than hoping a good early roll happens. This is the SAME lever proven to work in
     kvm_agent/models/holo.py's prior note-required fix (9/9), applied to the new mechanism.
  2. `tool_calls` is constrained to exactly one item (minItems=1, maxItems=1), not native's
     unbounded batching (multiple tool calls per step). Deliberately NOT adopted -- a
     separate, larger architectural decision (single-action-per-step is this project's
     existing design, tied into per-step frame-diff verification in agent_loop_holo.py's
     _execute()) than "use the reference request mechanism."
  3. press_key/scroll/drag_and_drop tools: our own extensions, not in native's documented
     tool set (native has richer coverage -- double_click/move_to/key_down/key_up/
     hotkey_desktop/update_plan/load_skill -- none of which are implemented here). Kept
     because real WAA runs needed them (see press_key's own comment below); not claimed to
     be native-equivalent.
  4. SYSTEM_PROMPT is condensed/adapted from native's ~26,000-char reference (captured
     verbatim in native_system_prompt_full.txt), not a byte-for-byte copy -- see that
     comment inline below for what's a direct port vs. a paraphrase.
  5. trim_to_last_n_images(n=1) ("goldfish memory") -- native's docs suggest keeping the
     last 3 screenshots; kept at 1 for vision-token cost, unchanged from the prior version.
  6. The frame-diff "screen changed / did not visibly change" signal (agent_loop_holo.py's
     _execute()) is folded into the CURRENT turn's <observation> text via
     observation_message()'s prev_result param, not a synthetic extra message role -- native
     has no execution-result feedback channel at all (the model only ever sees the next
     screenshot and must judge for itself via its own `thought`). Keeping SOME real result
     signal (added in an earlier session specifically because native's own docs flag exactly
     this gap as a cause of loops/forgetting) while not inventing a message role native
     doesn't have is the least-contaminating way to keep it.

Everything else matches native's actual captured request: JSON-schema-constrained
structured output (not function-calling), temperature=0.8, enable_thinking=True (matches
native's own agent-loop config, not the separate stateless element-localization endpoint's
temperature=0.0/no-thinking), <observation>...</observation> wrapping every screenshot-
bearing turn, the [0,1000]->pixel coordinate formula (verified against native and the
bring-up's own calibration, unchanged).

Normalized action shapes (mirrors the prior EvoCUA-era agent loop's action dict, so an
eventual loop is a drop-in):
    {"action": "left_click", "coordinate": [x, y], "element": "...", "note": "..."}
    {"action": "type", "text": "...", "press_enter": bool, "note": "..."}
    {"action": "key", "key": "ctrl+a", "note": "..."}   # single key or '+'-joined combo
    {"action": "scroll", "direction": "up"|"down"|"left"|"right", "note": "..."}
    {"action": "drag", "start": [x1, y1], "coordinate": [x2, y2], "note": "..."}
    {"action": "finished", "text": "...", "note": "..."}
    # "note" is present on every successfully-parsed action (top-level required field in
    # the response schema, not a per-tool param anymore) unless the model's response failed
    # to parse at all (-> {"action": "error", ...}, no note).

Format contract (verified empirically -- tools/probe_holo_structured_output.py, live,
2026-07-19, not assumed from vendor docs):
    - message.content is a JSON-encoded STRING matching RESPONSE_SCHEMA, must be
      json.loads()'d: {"note": str, "thought": str, "tool_calls": [{"tool_name": str, ...}]}.
    - message.reasoning_content (the <think> trace) is still populated separately from
      content in structured-output mode, same as it was in function-calling mode.
    - Coordinates are integers in [0, 1000], normalized to the exact image sent;
      projection is raw / 1000 * image_dimension.

STATUS: Phases I0-I5 of HOLO_INTEGRATION_PLAN.md are done and verified live (VM target,
capture-card observation, Pi 5 HID appliance). This structured-output rearchitecture landed
after I5, replacing the function-calling vendor-alignment pass that preceded it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from kvm_agent.config import CFG
from kvm_agent.llm.ollama import openai_client

logger = logging.getLogger("holo")


class _RequestLog:
    """Append-only JSONL log of every actual request/response to the model (2026-07-19,
    the same "log everything, verify what was actually sent" fix applied to hid_bridge.py/
    appliance.py earlier this session -- see those files' _CommandLog/CommandLogger). Before
    this, NOTHING captured the outgoing request: RunRecorder's step_NN.json only saves the
    model's RESPONSE (message/action/usage), never the messages list actually sent -- so
    whether the system prompt, notes block, or response schema were correctly constructed
    for a given step could not be verified after the fact, only assumed from reading the
    code. image_url data: URIs are redacted to a byte count (not omitted, not kept in full)
    -- multi-MB base64 blobs per step would make this log impractical, and the actual pixels
    are separately available via RunRecorder's saved step_NN.png; the TEXT structure (system
    prompt, <observation>/<notes> wrapper, response schema, temperature) is what "verify the
    prompt" actually needs, and none of that is touched by the redaction."""

    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.dirname(CFG.runs_dir), "logs", "holo_requests.jsonl")
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

# Per-tool_call schemas, native's "tool_name" const-discriminator shape (captured live from
# native_system_prompt_full.txt's <output_format> block, 2026-07-19). click/write/answer
# field names and descriptions match native's click_desktop/write_desktop/answer verbatim
# where native has an equivalent; press_key/scroll/drag_and_drop are our own extensions (see
# module docstring point 3) using OUR prior field names, not native's differently-shaped
# hotkey_desktop/scroll_desktop/drag_to_desktop (native's scroll_desktop takes a single
# anchor point + scroll_size in wheel clicks; native's drag_to_desktop drags FROM the
# current cursor position, not a specified start -- neither adopted here, unchanged
# behavior from the prior function-calling tool set).
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
            "press_enter": {"type": "boolean", "default": False, "description": "Whether to press Enter after typing"},
        },
    },
    {
        # press_key: our own extension, added 2026-07-19 after a WAA notepad run burned 15
        # click/drag steps (waa__366de66e..._205131 steps 25-39) trying to clear a Save-
        # dialog filename field with the mouse -- the action space had no keyboard path
        # besides write()'s press_enter. Backed by ENV.r4.key()/combo()
        # (kvm_agent/hardware/pico_client.py), which already supports named keys + held
        # combos; this just exposes it to the model.
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "key"],
        "properties": {
            "tool_name": {"const": "press_key"},
            "key": {
                "type": "string",
                "description": (
                    "Key name or '+'-joined combo, e.g. 'ctrl+a'. Single keys like 'enter', "
                    "'esc', 'tab', 'backspace', 'delete', 'up'/'down'/'left'/'right', 'home', "
                    "'end'; or combos like 'ctrl+s', 'ctrl+shift+t', 'alt+F4'."
                ),
            },
        },
    },
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_name", "direction", "x", "y"],
        "properties": {
            "tool_name": {"const": "scroll"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "x": {"type": "integer", "description": "X coordinate of the point to scroll at, in [0, 1000]"},
            "y": {"type": "integer", "description": "Y coordinate of the point to scroll at, in [0, 1000]"},
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

# Top-level response schema: every step is ONE JSON object with note/thought/tool_calls,
# all three required (see module docstring point 1 for why `note` is non-nullable here,
# unlike native's `["string","null"]`). tool_calls constrained to exactly one item (point 2).
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
                    "type": "string",
                    "description": (
                        "Required: task-relevant information to persist before this screenshot "
                        "is gone from memory (only the last screenshot is kept -- see the system "
                        "prompt). Record values, short text, file paths, dialog messages, "
                        "confirmations, button/field states -- anything you'll need later but "
                        "can't re-read once the screen changes. If genuinely nothing new is worth "
                        "persisting, write a short note anyway (e.g. 'no new state to record')."
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

# Condensed/adapted from H Company's own holo-desktop-cli system prompt (captured live
# 2026-07-19 via a logging proxy -- native_system_prompt_full.txt, ~26,000 chars). Direct
# ports, not paraphrases: the loop-detection line ("Detect loops...") and the "only the last
# screenshot is kept" framing for notes are native's actual wording, condensed. The
# termination checklist below is CONDENSED from native's much longer "Termination criteria"
# section (6 numbered conditions + a "do NOT call answer if" list + a courtroom-evidence
# framing) -- same intent, far fewer words, since each system-prompt token here is paid on
# EVERY step (single-turn-per-call architecture), unlike native which can send it once per
# session in some integrations.
SYSTEM_PROMPT = (
    "You are a GUI agent. You control the computer shown in screenshots. "
    "Given a screenshot and an instruction, decide the next action. "
    "Coordinates x and y must be integers in [0, 1000], normalized to the screenshot dimensions. "
    "You must always emit `note`, `thought`, and exactly one `tool_calls` entry -- both `note` "
    "and `thought` are mandatory every step, even when note has nothing new to add. "
    "If the screenshot already shows the instruction accomplished, call the `answer` tool "
    "immediately with a brief confirmation instead of taking another action.\n\n"
    "Detect loops: have you performed this same action before? If yes and it failed previously, "
    "you MUST pivot to a different approach -- try a different coordinate, a different control, "
    "a keyboard shortcut instead of a click, or back out (Escape) and re-enter the flow. Do not "
    "repeat an action that already failed twice in a row.\n\n"
    "Only the current screenshot is kept in memory -- earlier ones are gone and you cannot "
    "re-check them. Use `note` every step to persist values, text, file paths, dialog messages, "
    "or button/field states you will need later, before the screen that shows them is gone. "
    "Notes accumulate and are always visible to you.\n\n"
    "Before calling `answer`, confirm: the requested state is actually reached (not just "
    "attempted), you have concrete on-screen evidence for it (not an assumption), and no cheaper "
    "alternative action remains untried. Prefer 'I confirmed X because the screen showed Y' over "
    "'I believe X should have worked.'"
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


def observation_message(image_data_url: str, text: str = "", notes: list[str] | None = None,
                         prev_result: str | None = None) -> dict:
    """One <observation> user turn, per native's documented chat layout: every
    screenshot-bearing user turn is wrapped in <observation>...</observation>. `text` is
    normally the task instruction on the first turn of a run and empty on later turns (the
    model already has it via history).

    `notes` (ported from native holo-desktop-cli, 2026-07-19): accumulated text the model
    chose to persist via the top-level `note` field on prior steps. Rendered on EVERY turn
    THIS FUNCTION BUILDS FOR LIVE USE, but the caller (agent_loop_holo.py's run()) only
    passes it for the CURRENT/latest turn, not when re-appending past turns to history --
    matches the prior function-calling version's behavior (the <notes> block appears once,
    on the newest turn, not duplicated across every historical message).

    `prev_result` (2026-07-19, carried over from the function-calling version's "tool" role
    message -- see module docstring point 6): a short "screen changed / did not visibly
    change" signal from the PREVIOUS step's execution, folded into this turn's own
    <observation> text rather than a synthetic message role native doesn't have. None on
    the first step of a run (no previous action yet)."""
    parts = []
    if notes:
        parts.append("<notes>\n" + "\n".join(f"- {n}" for n in notes) + "\n</notes>\n")
    if prev_result:
        parts.append(prev_result + "\n")
    if text:
        parts.append(text)
    prefix = "<observation>\n" + "\n".join(parts) + "\n" if parts else "<observation>\n"
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prefix},
            {"type": "image_url", "image_url": {"url": image_data_url}},
            {"type": "text", "text": "\n</observation>"},
        ],
    }


def build_messages(instruction: str, image_data_url: str, history: list[dict] | None = None,
                    notes: list[str] | None = None, prev_result: str | None = None) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append(observation_message(image_data_url, instruction, notes=notes, prev_result=prev_result))
    return messages


def trim_to_last_n_images(messages: list[dict], n: int = 1) -> None:
    """In place: keep only the last n image_url chunks across all messages, replacing
    older ones with a "[screenshot evicted]" text chunk. Native's docs say "keep at most the
    last 3 screenshots"; we default to 1 (2026-07-18, "goldfish memory"): screenshots are
    the dominant prompt-token cost per step (~35% fewer at 1/4 resolution, and each extra
    kept screenshot re-pays its vision tokens every step), while the text-based history
    (<observation> wrappers, assistant JSON turns) still carries the narrative of what was
    tried. The <observation> text wrapper around each stays, only the image chunk itself
    gets replaced. Assistant turns (content is a plain string in structured-output mode, not
    a list) are untouched by this -- only user/<observation> turns carry image_url chunks."""
    image_positions = [
        (mi, ci)
        for mi, m in enumerate(messages)
        for ci, chunk in enumerate(m.get("content") or [])
        if isinstance(m.get("content"), list) and chunk.get("type") == "image_url"
    ]
    for mi, ci in image_positions[:-n] if n > 0 else image_positions:
        messages[mi]["content"][ci] = {"type": "text", "text": "[screenshot evicted]"}


def parse_response(message: dict, image_w: int, image_h: int) -> dict:
    """Normalize one assistant message (structured output) into an action dict.

    Never returns None silently -- an unparseable or empty response becomes
    {"action": "error", ...} and bumps dropped_actions, per the loud-failure guardrail.
    message["content"] is the JSON string the schema constrains the model to emit; unlike
    the prior function-calling version there's no message.tool_calls to look at.
    """
    raw_content = message.get("content") or ""
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as e:
        dropped_actions.bump(f"unparseable content JSON: {e}", message)
        return {"action": "error", "reason": "bad_content_json", "raw": message}

    tool_calls = parsed.get("tool_calls") or []
    if not tool_calls:
        dropped_actions.bump("no tool_calls in parsed content", message)
        return {"action": "error", "reason": "no_tool_calls", "raw": message}

    tc = tool_calls[0]
    name = tc.get("tool_name")
    note = (parsed.get("note") or "").strip() or None

    if name == "click":
        if "x" not in tc or "y" not in tc:
            dropped_actions.bump("click missing x/y", message)
            return {"action": "error", "reason": "click_missing_xy", "raw": message}
        out = {
            "action": "left_click",
            "coordinate": project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h),
            "element": tc.get("element"),
        }
    elif name == "write":
        out = {"action": "type", "text": tc.get("content", ""), "press_enter": tc.get("press_enter", False)}
    elif name == "press_key":
        key = tc.get("key", "")
        if not key:
            dropped_actions.bump("press_key missing key", message)
            return {"action": "error", "reason": "press_key_missing_key", "raw": message}
        out = {"action": "key", "key": key}
    elif name == "scroll":
        out = {"action": "scroll", "direction": tc.get("direction")}
        # Targeted scroll (flaw #10 fix, 2026-07-18): the wheel turns wherever the cursor
        # happens to sit, so an untargeted scroll silently scrolls the wrong pane -- or
        # nothing at all (11 zero-effect scrolls in the scroll_to_about battery task).
        if "x" in tc and "y" in tc:
            out["coordinate"] = project_point(_scalar(tc["x"]), _scalar(tc["y"]), image_w, image_h)
    elif name == "drag_and_drop":
        out = {
            "action": "drag",
            "start": project_point(_scalar(tc["x1"]), _scalar(tc["y1"]), image_w, image_h),
            "coordinate": project_point(_scalar(tc["x2"]), _scalar(tc["y2"]), image_w, image_h),
        }
    elif name == "answer":
        out = {"action": "finished", "text": tc.get("content", "")}
    else:
        dropped_actions.bump(f"unknown tool_name {name!r}", message)
        return {"action": "error", "reason": "unknown_tool", "tool_name": name, "raw": message}

    if note:
        out["note"] = note
    return out


def _target_config(target: str):
    if target == "local":
        return CFG.holo_local_url, CFG.holo_model, None
    if target == "hosted":
        return CFG.holo_hosted_url, CFG.holo_hosted_model, (CFG.hai_api_key or None)
    raise ValueError(f"unknown target {target!r} (expected 'local' or 'hosted')")


def call_holo_full(instruction: str, image_data_url: str, image_w: int, image_h: int,
                    target: str = "local", history: list[dict] | None = None,
                    temperature: float = 0.8, enable_thinking: bool = True,
                    max_history_images: int = 1, notes: list[str] | None = None,
                    prev_result: str | None = None) -> tuple[dict, dict, dict]:
    """Like call_holo, but also returns the raw assistant message (dict, via model_dump())
    and token usage -- a multi-step loop needs the message to thread real history (the
    assistant's own JSON content re-added to history each step; see agent_loop_holo.py's
    run()) and usage for run instrumentation (see kvm_agent/instrumentation/run_log.py).
    call_holo() itself discards both since the REPL/single-shot callers only need the
    parsed action.

    Uses response_format=json_schema (structured output), NOT function-calling/tools=
    (2026-07-19 rearchitecture -- see module docstring). Defaults (temperature=0.8,
    enable_thinking=True) match native's documented agent-loop config, NOT the separate
    element-localization endpoint's (temperature=0.0, no thinking) -- those are for a
    stateless single-shot grounding primitive, a different use case from this multi-step
    loop. "Reasoning is essential in agent mode (Holo was trained to plan before each
    step), so leave it on" -- past reasoning is dropped between turns by the chat
    template regardless (only the parsed JSON content gets re-added to history, never
    reasoning_content), so this only affects the CURRENT step's decision quality, not
    what's threaded forward.

    LATENCY NOTE (2026-07-17, measured against this rig under the prior function-calling
    mechanism -- not re-measured under structured output, may differ): image FORMAT doesn't
    matter locally -- PNG vs JPEG at the same resolution produced byte-identical
    prompt_tokens (2842 both), since the vision encoder sees decoded pixels either way and
    loopback transfer of a few MB is sub-millisecond regardless. Image RESOLUTION does
    matter -- 960x540 (1/4 the pixels of 1920x1080) used ~35% fewer prompt_tokens (1834 vs
    2842). completion_tokens (the reasoning trace, generated token-by-token -- sequential,
    slow) varied 90-154 across otherwise-identical calls -- reasoning-length variance at
    temperature=0.8 looks like a bigger latency lever than image size/format.

    `notes` (2026-07-19, ported from native holo-desktop-cli): accumulated persisted text
    from prior steps' `note` fields, rendered on every turn regardless of image eviction --
    the caller (agent_loop_holo.py's run()) owns the growing list and passes it each call.

    `prev_result` (2026-07-19): the frame-diff "screen changed / did not visibly change"
    signal from the immediately-prior step's execution -- see observation_message's
    docstring for why this is folded into <observation> text rather than a synthetic
    message role.
    """
    base_url, model, api_key = _target_config(target)
    client = openai_client(base_url=base_url, api_key=api_key or "unused")
    messages = build_messages(instruction, image_data_url, history=history, notes=notes, prev_result=prev_result)
    trim_to_last_n_images(messages, n=max_history_images)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, response_format=RESPONSE_SCHEMA,
            max_tokens=4096, temperature=temperature,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
        )
    except Exception as e:
        REQUEST_LOG.write({"target": target, "model": model, "messages": REQUEST_LOG._redact(messages),
                           "response_format": RESPONSE_SCHEMA, "temperature": temperature,
                           "enable_thinking": enable_thinking, "error": str(e),
                           "http_ms": round((time.time() - t0) * 1000.0, 1)})
        raise
    message = resp.choices[0].message.model_dump()
    action = parse_response(message, image_w, image_h)
    usage = resp.usage.model_dump() if resp.usage else {}
    REQUEST_LOG.write({"target": target, "model": model, "messages": REQUEST_LOG._redact(messages),
                       "response_format": RESPONSE_SCHEMA, "temperature": temperature,
                       "enable_thinking": enable_thinking, "response_message": message,
                       "parsed_action": action, "usage": usage,
                       "http_ms": round((time.time() - t0) * 1000.0, 1)})
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
    """Offline self-test: re-parses captured structured-output raw examples, no network."""
    import os
    fixture = os.path.join(os.path.dirname(__file__), "_fixtures", "holo_structured_output_raw.json")
    with open(fixture) as f:
        examples = json.load(f)

    image_w, image_h = examples[0]["image_w"], examples[0]["image_h"]
    ok = 0
    for ex in examples:
        action = parse_response(ex["message"], ex["image_w"], ex["image_h"])
        assert action["action"] != "error", f"failed to parse: {ex['instruction']!r} -> {action}"
        print(f"OK  {ex['instruction']!r:70s} -> {action}")
        ok += 1

    assert dropped_actions.count == 0, f"self-test should not drop any captured example, got {dropped_actions.count}"
    print(f"\n{ok}/{len(examples)} examples parsed cleanly. dropped_actions={dropped_actions.count}")

    # Spot-check the click projection against the captured raw tool_call args.
    click_ex = next(e for e in examples
                     if json.loads(e["message"]["content"])["tool_calls"][0]["tool_name"] == "click")
    action = parse_response(click_ex["message"], click_ex["image_w"], click_ex["image_h"])
    raw_tc = json.loads(click_ex["message"]["content"])["tool_calls"][0]
    expected = [raw_tc["x"] / 1000 * click_ex["image_w"], raw_tc["y"] / 1000 * click_ex["image_h"]]
    assert action["coordinate"] == expected, (action["coordinate"], expected)
    print(f"Coordinate projection check OK: raw=({raw_tc['x']},{raw_tc['y']}) -> {action['coordinate']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
