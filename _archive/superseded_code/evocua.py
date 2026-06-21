"""
evocua.py — EvoCUA-8B (S2 mode) grounding adapter.

Replaces uitars.py. Built to match EvoCUA's actual S2 protocol (read from
mm_agents/evocua/): the model is prompted with a Qwen-style tool schema and
replies with an `Action:` line + a <tool_call> JSON block. We:
  1. smart_resize the screenshot the way EvoCUA's process_image does, send THAT
  2. parse the <tool_call> JSON
  3. scale coordinates from the processed image space back to target pixels
  4. return a do_action-compatible dict

COORDINATES (the important part): EvoCUA-S2 returns coords in the PROCESSED
image's pixel space (smart_resize'd, factor=32). adjust = original/processed
per axis. We send the processed image AND know its dims, so this is exact —
no empirical guessing like the Sonnet case.
"""

import re
import json
import math
import base64
from io import BytesIO
from PIL import Image

RESIZE_FACTOR = 32          # S2 mode (patch_size 16 * merge_size 2 = 32; matches official preprocessor_config)
MAX_PIXELS = 16 * 16 * 4 * 12800     # matches the official agent's process_image buffer

# Coordinate mode. The model's DEFAULT (and the official OSWorld eval) is
# "relative": the prompt states a 1000x1000 screen, the model emits a 0..999
# grid, and we scale to the real screen. This is the representation it was
# trained/RL'd on. "absolute" (state processed pixel dims, model emits processed
# pixels) is what we tried first — it grounds large/edge coordinates poorly.
COORD_TYPE = "relative"     # "relative" | "absolute"

# History protocol (matches upstream _build_s2_messages). The model reflects on
# whether its last action worked by SEEING the prior screenshot next to its own
# prior response — so we replay the last N turns as real image+assistant pairs,
# not a text summary. Token budget (the binding constraint here): each processed
# 1920x1080 frame is ~2040 vision tokens (factor-32 => 60*34 patches), and we're
# capped at num_ctx 8192 (16384 OOMs the 4080 on top of the Q8 weights). Budget
# at history_n=1: current+1 frame (~4080) + system/tool JSON (~700) + gen(1536)
# ≈ 6.3k < 8192, comfortable. history_n=1 still gives the key reflection signal
# (the immediately-prior frame + the model's own prior response). To go higher,
# either lower MAX_TOKENS or run Ollama with KV-cache quant
# (OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0) to fit more frames on GPU.
HISTORY_TURNS = 4          # SPEC: reference max_history_turns=4 (needs num_ctx 16384 to fit ~5 frames)
MAX_TOKENS    = 1536        # generation cap; keep prompt+gen under num_ctx 8192


def _round_by(n, f):  return round(n / f) * f
def _ceil_by(n, f):   return math.ceil(n / f) * f
def _floor_by(n, f):  return math.floor(n / f) * f


def smart_resize(height, width, factor=RESIZE_FACTOR, min_pixels=56 * 56,
                 max_pixels=MAX_PIXELS, max_long_side=8192):
    """Port of EvoCUA's smart_resize (qwen_vl_utils)."""
    if max(height, width) > max_long_side:
        beta = max(height, width) / max_long_side
        height, width = int(height / beta), int(width / beta)
    h_bar = _round_by(height, factor)
    w_bar = _round_by(width, factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = _floor_by(height / beta, factor)
        w_bar = _floor_by(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = _ceil_by(height * beta, factor)
        w_bar = _ceil_by(width * beta, factor)
    return h_bar, w_bar


def process_image(bgr_frame):
    """Resize a BGR frame the EvoCUA way; return (png_b64, proc_w, proc_h)."""
    # cv2 BGR -> PIL RGB
    import cv2
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    w, h = img.size
    ph, pw = smart_resize(h, w, factor=RESIZE_FACTOR, max_pixels=MAX_PIXELS)
    img = img.resize((pw, ph))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), pw, ph


# EvoCUA S2 prompts — VERBATIM from the official mm_agents/evocua/prompts.py
# (no longer a paraphrase). resolution_info is filled per COORD_TYPE.
S2_ACTION_DESCRIPTION = """
* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order.
* `key_down`: Press and HOLD the specified key(s) down in order (no release). Use this for stateful holds like holding Shift while clicking.
* `key_up`: Release the specified key(s) in reverse order.
* `type`: Type a string of text on the keyboard.
* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.
* `left_click`: Click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.
* `right_click`: Click the right mouse button at a specified (x, y) pixel coordinate on the screen.
* `middle_click`: Click the middle mouse button at a specified (x, y) pixel coordinate on the screen.
* `double_click`: Double-click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `triple_click`: Triple-click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `scroll`: Performs a scroll of the mouse scroll wheel.
* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).
* `wait`: Wait specified seconds for the change to happen.
* `terminate`: Terminate the current task and report its completion status.
* `answer`: Answer a question.
"""

S2_DESCRIPTION_PROMPT_TEMPLATE = """Use a mouse and keyboard to interact with a computer, and take screenshots.
* This is an interface to a desktop GUI. You must click on desktop icons to start applications.
* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn't open, try wait and taking another screenshot.
{resolution_info}
* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.
* If you tried clicking on a program or link but it failed to load even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked."""

S2_SYSTEM_PROMPT = """# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{tools_xml}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>

# Response format

Response format for every step:
1) Action: a short imperative describing what to do in the UI.
2) A single <tool_call>...</tool_call> block containing only the JSON: {{"name": <function-name>, "arguments": <args-json-object>}}.

Rules:
- Output exactly in the order: Action, <tool_call>.
- Be brief: one sentence for Action.
- Do not output anything else outside those parts.
- If finishing, use action=terminate in the tool call."""


def build_s2_tools_def(description_prompt):
    return {
        "type": "function",
        "function": {
            "name_for_human": "computer_use",
            "name": "computer_use",
            "description": description_prompt,
            "parameters": {
                "properties": {
                    "action": {
                        "description": S2_ACTION_DESCRIPTION,
                        "enum": ["key", "type", "mouse_move", "left_click", "left_click_drag",
                                 "right_click", "middle_click", "double_click", "triple_click", "scroll",
                                 "wait", "terminate", "key_down", "key_up"],
                        "type": "string"
                    },
                    "keys": {"description": "Required only by `action=key`.", "type": "array"},
                    "text": {"description": "Required only by `action=type`.", "type": "string"},
                    "coordinate": {"description": "The x,y coordinates for mouse actions.", "type": "array"},
                    "pixels": {"description": "The amount of scrolling.", "type": "number"},
                    "time": {"description": "The seconds to wait.", "type": "number"},
                    "status": {"description": "The status of the task.", "type": "string",
                               "enum": ["success", "failure"]}
                },
                "required": ["action"],
                "type": "object"
            },
            "args_format": "Format the arguments as a JSON object."
        }
    }


def build_system_prompt(proc_w, proc_h):
    if COORD_TYPE == "absolute":
        resolution_info = f"* The screen's resolution is {proc_w}x{proc_h}."
    else:
        resolution_info = "* The screen's resolution is 1000x1000."
    description_prompt = S2_DESCRIPTION_PROMPT_TEMPLATE.format(resolution_info=resolution_info)
    tools_def = build_s2_tools_def(description_prompt)
    return S2_SYSTEM_PROMPT.format(tools_xml=json.dumps(tools_def))


def _scale(coord, proc_w, proc_h, tgt_w, tgt_h):
    x, y = coord
    if COORD_TYPE == "absolute":
        # model emits processed-image pixels -> scale to the original screen
        return [int(x * tgt_w / proc_w), int(y * tgt_h / proc_h)]
    # relative: model emits a 0..999 grid -> scale to the original screen
    return [int(x * tgt_w / 999), int(y * tgt_h / 999)]


def parse_action(response, proc_w, proc_h, tgt_w, tgt_h):
    """EvoCUA S2 response -> do_action-compatible dict (coords in target px)."""
    m = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", response, re.DOTALL)
    if not m:
        m2 = re.search(r"(\{\s*\"name\".*\"arguments\".*\})", response, re.DOTALL)
        if not m2:
            return None
        blob = m2.group(1)
    else:
        blob = m.group(1)
    try:
        call = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if call.get("name") != "computer_use":
        return None
    args = call.get("arguments", {})
    a = args.get("action")

    def sc(c): return _scale(c, proc_w, proc_h, tgt_w, tgt_h)

    if a in ("left_click", "click"):
        return {"action": "left_click", "coordinate": sc(args["coordinate"])} if "coordinate" in args else None
    if a == "right_click":
        return {"action": "right_click", "coordinate": sc(args["coordinate"])} if "coordinate" in args else None
    if a == "double_click":
        return {"action": "double_click", "coordinate": sc(args["coordinate"])} if "coordinate" in args else None
    if a == "triple_click":
        c = sc(args["coordinate"]); return {"action": "triple_click", "coordinate": c}
    if a == "mouse_move":
        return {"action": "mouse_move", "coordinate": sc(args["coordinate"])} if "coordinate" in args else None
    if a == "left_click_drag":
        return {"action": "left_click_drag", "coordinate": sc(args["coordinate"])} if "coordinate" in args else None
    if a == "type":
        return {"action": "type", "text": args.get("text", "")}
    if a in ("key", "key_down", "key_up"):
        keys = args.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        return {"action": "key", "text": "+".join(k.strip() for k in keys)}
    if a == "scroll":
        px = args.get("pixels", 0)
        return {"action": "scroll",
                "scroll_direction": "up" if px > 0 else "down",
                "scroll_amount": max(1, abs(int(px)) // 100 or 3)}
    if a == "wait":
        return {"action": "wait"}
    if a == "terminate":
        return {"action": "finished", "text": args.get("status", "success")}
    return None


def extract_action_line(response):
    """Pull the model's own `Action:` sentence out of a raw response.

    This is what upstream stores as `low_level_instruction` and feeds back as
    the text summary of older steps — the model's stated intent, NOT our
    mechanical 'click 734,663' paraphrase.
    """
    if not response:
        return "Acting"
    for line in response.splitlines():
        s = line.strip()
        if s.lower().startswith("action:"):
            return s.split(":", 1)[1].strip() or "Acting"
    return "Acting"


def build_messages(system_prompt, screenshots, responses, instruction, history_n):
    """Port of EvoCUA-S2 _build_s2_messages (evocua_agent.py).

    screenshots: processed-image b64 for every step so far, INCLUDING the
                 current frame as the last element.
    responses:   raw model responses for every COMPLETED step (excludes current).

    The last `history_n` turns are replayed as real multimodal turns (past
    screenshot as image-only user msg + the model's own response as assistant
    msg); only the older steps are collapsed into a 'Previous actions' text
    summary built from the model's Action: lines.
    """
    msgs = [{"role": "system", "content": [{"type": "text", "text": system_prompt}]}]

    step = len(responses)                       # number of completed steps
    current_img = screenshots[-1]

    history_start_idx = max(0, step - history_n)
    previous_actions = [
        f"Step {i + 1}: {extract_action_line(responses[i])}"
        for i in range(history_start_idx)
    ]
    previous_actions_str = "\n".join(previous_actions) if previous_actions else "None"

    instruction_prompt = (
        "\nPlease generate the next move according to the UI screenshot, "
        "instruction and previous actions.\n\n"
        f"Instruction: {instruction}\n\n"
        f"Previous actions:\n{previous_actions_str}"
    )

    history_len = min(history_n, len(responses))
    if history_len > 0:
        hist_responses = responses[-history_len:]
        hist_imgs = screenshots[-history_len - 1:-1]   # frames BEFORE current
        for i in range(history_len):
            if i < len(hist_imgs):
                url = f"data:image/png;base64,{hist_imgs[i]}"
                content = [{"type": "image_url", "image_url": {"url": url}}]
                if i == 0:                              # carry instruction on first
                    content.append({"type": "text", "text": instruction_prompt})
                msgs.append({"role": "user", "content": content})
            msgs.append({"role": "assistant",
                         "content": [{"type": "text", "text": hist_responses[i]}]})

    cur_url = f"data:image/png;base64,{current_img}"
    if history_len == 0:
        msgs.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": cur_url}},
            {"type": "text", "text": instruction_prompt},
        ]})
    else:
        msgs.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": cur_url}},
        ]})
    return msgs


def ground(client, model, screenshots, responses, proc_w, proc_h, instruction,
           history_n=HISTORY_TURNS):
    """Grounding call with multi-turn history. Decrements history_n and retries
    if the context overflows (Ollama 500 / context error), mirroring upstream."""
    sysp = build_system_prompt(proc_w, proc_h)
    n = history_n
    while True:
        msgs = build_messages(sysp, screenshots, responses, instruction, n)
        try:
            r = client.chat.completions.create(model=model, messages=msgs,
                                               max_tokens=MAX_TOKENS,
                                               temperature=0.01, top_p=0.9)   # SPEC: reference temp 0.01
            return r.choices[0].message.content
        except Exception as e:
            if n > 0:
                n -= 1
                print(f"  [ctx] grounding failed ({type(e).__name__}); "
                      f"retrying with history_n={n}")
                continue
            raise


# parser self-test
if __name__ == "__main__":
    PW, PH, TW, TH = 1280, 736, 1920, 1080   # example processed vs target
    samples = [
        'Action: click firefox\n<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[600,690]}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"type","text":"en.wikipedia.org"}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"key","keys":["ctrl","s"]}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"scroll","pixels":-300}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"terminate","status":"success"}}</tool_call>',
    ]
    for s in samples:
        print(parse_action(s, PW, PH, TW, TH))
