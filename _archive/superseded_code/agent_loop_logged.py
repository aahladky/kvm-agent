"""
agent_loop_logged.py — instrumented agent loop with prompt caching.

Adds prompt caching to cut the cost of re-sending stable context every turn:
  - the tools definition is cached once (static across the run)
  - a rolling cache breakpoint marks the newest message each turn, so the whole
    prior conversation bills at the cheap cache-read rate; only the new turn is
    full price.

Caching needs a byte-stable prefix, and the screenshot trimmer mutates old
messages — those fight each other. So when CACHE is on, trimming is skipped and
caching does the cost work instead. For very long runs you'd flip CACHE off and
let trimming bound the token count; for short tasks, caching wins.

Default config = Opus 4.8 + 720p downscale. Swap the CONFIG lines for Sonnet.
"""

import base64
import threading
import time
import cv2
import anthropic
from r4_client import R4
from run_logger import RunLogger

# ---- EDIT ----
GOAL = "Open the calculator, compute 1847 × 23, then open the text editor and type the result."
CAM_INDEX     = 0
MAX_ITERS     = 25
CONFIRM_FIRST = 0
SETTLE_SEC    = 0.75
KEEP_IMAGES   = 3       # only used when CACHE = False
CACHE         = True    # prompt caching (skips trimming when on)
# --------------

# ---- CONFIG: Opus (default) ----
# MODEL          = "claude-opus-4-8"
# SEND_W, SEND_H = 1280, 720
# ---- CONFIG: Sonnet (uncomment to A/B) ----
MODEL          = "claude-sonnet-4-6"
SEND_W, SEND_H = 1280, 720
# -------------------------------------------

CAP_W, CAP_H = 1920, 1080
SCALE = CAP_W / SEND_W
assert abs(CAP_H / SEND_H - SCALE) < 1e-6, "non-uniform scale — fix SEND dims"

BETA      = "computer-use-2025-11-24"
TOOL_TYPE = "computer_20251124"

KEYMAP = {
    "Return": 176, "Enter": 176, "Escape": 177, "Esc": 177,
    "BackSpace": 178, "Backspace": 178, "Tab": 179, "Delete": 212,
    "Up": 218, "Down": 217, "Left": 216, "Right": 215,
    "Home": 210, "End": 213, "Page_Up": 211, "Page_Down": 214,
    "space": 32, "Space": 32,
}


class Camera:
    def __init__(self, index):
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_H)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.run:
            ok, f = self.cap.read()
            if ok:
                self.frame = f

    def read(self):
        return self.frame

    def release(self):
        self.run = False
        time.sleep(0.1)
        self.cap.release()


def settle_and_show(cam, seconds):
    end = time.time() + seconds
    while time.time() < end:
        f = cam.read()
        if f is not None:
            cv2.imshow("capture", f)
        cv2.waitKey(15)


def img_block(frame):
    if (SEND_W, SEND_H) != (CAP_W, CAP_H):
        frame = cv2.resize(frame, (SEND_W, SEND_H), interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", frame)
    b64 = base64.b64encode(png.tobytes()).decode()
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": b64}}


def tool_result_img(tool_use_id, frame):
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": [img_block(frame)]}


def trim_screenshots(messages, keep):
    img_blocks = []
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for blk in m["content"]:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    if any(isinstance(c, dict) and c.get("type") == "image"
                           for c in blk.get("content", [])):
                        img_blocks.append(blk)
    for blk in (img_blocks[:-keep] if keep > 0 else img_blocks):
        blk["content"] = [{"type": "text",
                           "text": "[earlier screenshot omitted to save tokens]"}]


# ---------- prompt caching: rolling breakpoint on the newest message ----------

def clear_cache_marks(messages):
    for m in messages:
        c = m["content"]
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    blk.pop("cache_control", None)


def mark_last_message(messages):
    # cache_control on the last top-level block of the most recent message;
    # that message is always a user dict (initial prompt, or tool_results).
    content = messages[-1]["content"]
    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}


def do_action(r4, inp, scale):
    a = inp.get("action")
    coord = inp.get("coordinate")
    x = y = None
    if coord:
        x, y = int(coord[0] * scale), int(coord[1] * scale)

    if a == "screenshot":
        return "screenshot"
    if a == "wait":
        time.sleep(float(inp.get("duration", 1)))
        return "wait"
    if a == "mouse_move":
        r4.move(x, y); return f"move {x},{y}"
    if a == "left_click":
        r4.move(x, y); r4.click(); return f"click {x},{y}"
    if a == "right_click":
        r4.move(x, y); r4.rclick(); return f"right_click {x},{y}"
    if a == "double_click":
        r4.move(x, y); r4.click(); r4.click(); return f"double_click {x},{y}"
    if a == "triple_click":
        r4.move(x, y); r4.click(); r4.click(); r4.click(); return f"triple_click {x},{y}"
    if a == "left_mouse_down":
        r4.move(x, y); r4.down(); return f"mouse_down {x},{y}"
    if a == "left_mouse_up":
        r4.up(); return "mouse_up"
    if a == "left_click_drag":
        s = inp.get("start_coordinate")
        if s:
            r4.drag(int(s[0] * scale), int(s[1] * scale), x, y)
            return f"drag->{x},{y}"
    if a == "type":
        r4.type(inp.get("text", "")); return f"type {inp.get('text','')!r}"
    if a == "key":
        t = inp.get("text", "")
        if "+" in t:                       # modifier combo, now supported
            r4.combo(t); return f"combo {t}"
        if t in KEYMAP:
            r4.key(KEYMAP[t]); return f"key {t}"
        if len(t) == 1:
            r4.key(ord(t)); return f"key {t}"
        print(f"  [skip] unmapped key '{t}'")
        return "skip-key"
    if a == "scroll":
        if coord:
            r4.move(x, y)                  # scroll happens at the cursor
        direction = inp.get("scroll_direction", "down")
        amount = int(inp.get("scroll_amount", 3))
        if direction in ("up", "down"):
            r4.scroll(amount if direction == "up" else -amount)
            return f"scroll {direction} {amount}"
        print(f"  [skip] horizontal scroll '{direction}' not wired")
        return f"skip-scroll-{direction}"
    print(f"  [skip] unsupported action '{a}'")
    return f"skip-{a}"


def preview(frame, inp, scale):
    f = frame.copy()
    coord = inp.get("coordinate")
    if coord:
        x, y = int(coord[0] * scale), int(coord[1] * scale)
        cv2.circle(f, (x, y), 24, (0, 0, 255), 3)
        cv2.line(f, (x - 36, y), (x + 36, y), (0, 0, 255), 2)
        cv2.line(f, (x, y - 36), (x, y + 36), (0, 0, 255), 2)
    cv2.imshow("capture", f)
    cv2.waitKey(1)
    print(f"  PENDING: action={inp.get('action')} "
          f"coord={inp.get('coordinate','')} text={inp.get('text','')!r}")
    print("  check the preview window, then press Enter here (Ctrl-C aborts)")
    input()


def main():
    print(f"MODEL: {MODEL}   CACHE: {CACHE}")
    print(f"GOAL: {GOAL}\n")
    log = RunLogger(MODEL, GOAL)
    r4 = R4()
    print("R4 connected")

    cam = Camera(CAM_INDEX)
    t0 = time.time()
    while cam.read() is None:
        if time.time() - t0 > 5:
            raise SystemExit("no frames from card — is OBS holding it?")
        time.sleep(0.05)
    print("capture open (threaded)")

    client = anthropic.Anthropic()
    frame = cam.read()

    tool = {"type": TOOL_TYPE, "name": "computer",
            "display_width_px": SEND_W, "display_height_px": SEND_H,
            "display_number": 1}
    if CACHE:
        tool["cache_control"] = {"type": "ephemeral"}   # cache the tools prefix
    tools = [tool]

    messages = [{"role": "user", "content": [
        {"type": "text", "text": GOAL},
        img_block(frame),
    ]}]
    acted = 0
    finished = False

    try:
        for it in range(MAX_ITERS):
            if CACHE:
                clear_cache_marks(messages)
                mark_last_message(messages)
            else:
                trim_screenshots(messages, KEEP_IMAGES)

            t_api = time.time()
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=2048, betas=[BETA],
                tools=tools, messages=messages,
            )
            latency = time.time() - t_api
            messages.append({"role": "assistant", "content": resp.content})

            think = " ".join(b.text.strip() for b in resp.content
                             if b.type == "text" and b.text.strip())
            if think:
                print(f"[{it}] think: {think}")

            if resp.stop_reason != "tool_use":
                finished = True
                log.step(it, think, ["DONE"], None, resp.usage, latency, cam.read())
                print("\nMODEL DONE.")
                break

            results = []
            iter_actions = []
            primary_coord = None
            latest = cam.read()
            for b in resp.content:
                if b.type == "tool_use":
                    current = cam.read()
                    if acted < CONFIRM_FIRST:
                        preview(current, b.input, SCALE)
                    desc = do_action(r4, b.input, SCALE)
                    iter_actions.append(desc)
                    c = b.input.get("coordinate")
                    if c and primary_coord is None:
                        primary_coord = [int(c[0] * SCALE), int(c[1] * SCALE)]
                    acted += 1
                    print(f"[{it}] did: {desc}")
                    settle_and_show(cam, SETTLE_SEC)
                    latest = cam.read()
                    results.append(tool_result_img(b.id, latest))
            log.step(it, think, iter_actions, primary_coord, resp.usage, latency, latest)
            messages.append({"role": "user", "content": results})
        else:
            print("\nHit MAX_ITERS cap — stopping.")
    finally:
        log.finish(finished)
        cam.release()
        r4.close()
        cv2.destroyAllWindows()
        print("released capture + R4")


if __name__ == "__main__":
    main()
