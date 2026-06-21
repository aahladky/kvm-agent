"""
agent_loop.py — closed-loop computer-use agent (v2).

Changes from v1:
  - Threaded Camera: ONE process opens the card, reads continuously into a
    shared latest-frame. Kills cold-open tearing structurally (stream always
    warm) and lets a preview window show the feed live.
  - Live preview window ("capture"): watch what the agent sees. Smooth during
    the settle window; freezes only during the API round-trip (imshow must run
    on the main thread, which is blocked while waiting on the model).
  - 720p downscale: frames captured at 1080p (Dell/chain happy), resized to
    1280x720 before the API call for cheaper tokens. Model returns coords in
    720-space; we scale them UP x1.5 to the 1080p target space the R4 is
    calibrated for.
  - Screenshot trimming: only the last KEEP_IMAGES screenshots stay as images
    in history; older ones become text stubs. This is the bigger cost lever —
    it stops the per-turn image resend from growing without bound.

Still Opus 4.8 (coords 1:1 with the image we send, so the only transform is
the clean x1.5 upscale). Sonnet 4.6 would be cheaper still but applies its own
internal scale factor — swap that later as a separate change, not now.
"""

import base64
import threading
import time
import cv2
import anthropic
from r4_client import R4

# ---- EDIT ----
GOAL = "Open Firefox, go to en.wikipedia.org, search for 'SpaceX Dragon', and open the first result."
CAM_INDEX     = 0
MAX_ITERS     = 25      # browser chains need more steps than a single click
CONFIRM_FIRST = 1       # Enter-gate the first N actions (0 = full auto)
SETTLE_SEC    = 1.5     # browser paints are slower than a dock highlight
KEEP_IMAGES   = 3       # screenshots kept as images in history (rest -> text)
# --------------

CAP_W, CAP_H   = 1920, 1080     # what the card captures / R4 is calibrated to
SEND_W, SEND_H = 1280, 720      # downscaled size sent to the API
SCALE = CAP_W / SEND_W          # 1.5: model(720) coord -> target(1080) coord
assert abs(CAP_H / SEND_H - SCALE) < 1e-6, "non-uniform scale — fix SEND dims"

MODEL     = "claude-opus-4-8"
BETA      = "computer-use-2025-11-24"
TOOL_TYPE = "computer_20251124"

KEYMAP = {
    "Return": 176, "Enter": 176, "Escape": 177, "Esc": 177,
    "BackSpace": 178, "Backspace": 178, "Tab": 179, "Delete": 212,
    "Up": 218, "Down": 217, "Left": 216, "Right": 215,
    "Home": 210, "End": 213, "Page_Up": 211, "Page_Down": 214,
    "space": 32, "Space": 32,
}


# ---------- threaded capture (one opener, always-warm) ----------

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
                self.frame = f          # always hold the newest frame

    def read(self):
        return self.frame

    def release(self):
        self.run = False
        time.sleep(0.1)
        self.cap.release()


def settle_and_show(cam, seconds):
    """Pump the preview window for `seconds` so the UI can update and you see it move."""
    end = time.time() + seconds
    while time.time() < end:
        f = cam.read()
        if f is not None:
            cv2.imshow("capture", f)
        cv2.waitKey(15)                 # ~60fps pump; REQUIRED to paint the window


# ---------- image helpers (downscale to 720p before sending) ----------

def img_block(frame):
    small = cv2.resize(frame, (SEND_W, SEND_H), interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", small)
    b64 = base64.b64encode(png.tobytes()).decode()
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": b64}}


def tool_result_img(tool_use_id, frame):
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": [img_block(frame)]}


def trim_screenshots(messages, keep):
    """Replace all but the last `keep` screenshot tool_results with a text stub."""
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


# ---------- action execution (scale 720->1080 before the R4) ----------

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
        if "+" in t:
            print(f"  [skip] key combo '{t}' — extend the R4 protocol for modifiers")
            return "skip-combo"
        if t in KEYMAP:
            r4.key(KEYMAP[t]); return f"key {t}"
        if len(t) == 1:
            r4.key(ord(t)); return f"key {t}"
        print(f"  [skip] unmapped key '{t}'")
        return "skip-key"
    print(f"  [skip] unsupported action '{a}'")
    return f"skip-{a}"


def preview(frame, inp, scale):
    f = frame.copy()
    coord = inp.get("coordinate")
    if coord:
        x, y = int(coord[0] * scale), int(coord[1] * scale)   # draw at 1080 pos
        cv2.circle(f, (x, y), 24, (0, 0, 255), 3)
        cv2.line(f, (x - 36, y), (x + 36, y), (0, 0, 255), 2)
        cv2.line(f, (x, y - 36), (x, y + 36), (0, 0, 255), 2)
    cv2.imshow("capture", f)
    cv2.waitKey(1)
    print(f"  PENDING: action={inp.get('action')} "
          f"coord={inp.get('coordinate','')} text={inp.get('text','')!r}")
    print("  check the preview window, then press Enter here (Ctrl-C aborts)")
    input()


# ---------- the loop ----------

def main():
    print(f"GOAL: {GOAL}\n")
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
    tools = [{
        "type": TOOL_TYPE, "name": "computer",
        "display_width_px": SEND_W, "display_height_px": SEND_H, "display_number": 1,
    }]
    messages = [{"role": "user", "content": [
        {"type": "text", "text": GOAL},
        img_block(frame),
    ]}]
    acted = 0

    try:
        for it in range(MAX_ITERS):
            trim_screenshots(messages, KEEP_IMAGES)
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=2048, betas=[BETA],
                tools=tools, messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    print(f"[{it}] think: {b.text.strip()}")

            if resp.stop_reason != "tool_use":
                print("\nMODEL DONE.")
                break

            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    current = cam.read()
                    if acted < CONFIRM_FIRST:
                        preview(current, b.input, SCALE)
                    desc = do_action(r4, b.input, SCALE)
                    acted += 1
                    print(f"[{it}] did: {desc}")
                    settle_and_show(cam, SETTLE_SEC)
                    results.append(tool_result_img(b.id, cam.read()))
            messages.append({"role": "user", "content": results})
        else:
            print("\nHit MAX_ITERS cap — stopping.")
    finally:
        cam.release()
        r4.close()
        cv2.destroyAllWindows()
        print("released capture + R4")


if __name__ == "__main__":
    main()
