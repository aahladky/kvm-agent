"""
agent_loop_evocua.py — local EvoCUA-8B loop (S2 mode) over Ollama.

Same harness as the Claude loop (Camera, do_action, R4, logging). Brain is
local EvoCUA-8B served by Ollama. Grounding + coordinate math live in evocua.py.

Key difference vs the Claude/UI-TARS path: EvoCUA's S2 mode pre-resizes the
screenshot (smart_resize, factor 32) and returns coords in THAT processed
space; evocua.process_image gives us the processed dims, and evocua.parse_action
scales coords back to the 1920x1080 target the R4 is calibrated for. So there is
NO separate SCALE here — the adapter already returns target-space pixels.
"""

import threading
import time
import cv2
from openai import OpenAI
from r4_client import R4
from run_logger import RunLogger
import evocua

# ---- EDIT ----
GOAL = "Using the open Calculator, compute 7 × 8 + 5"   # Calculator PRE-OPENED + cleared to 0 before each run, so every rung shares one start state and the quant signal is pure button-grounding, not launch luck. Expected display: 61. Answer kept OUT of the goal (verify-before-terminate test).
MODEL_URL   = "http://192.168.0.155:11434/v1"      # Ollama
MODEL_NAME  = "evocua-8b-q5-clean"   # A/B: clean (NO-imatrix) Q5_K_M vs imatrix Q5 — does + still misground?
CAM_INDEX     = 0
MAX_ITERS     = 25
CONFIRM_FIRST = 0          # FIRST run of a NEW task: gate to eyeball small-button grounding before the Pico clicks. Drop to 0 once grounding is confirmed.
SETTLE_SEC    = 1
# --------------

CAP_W, CAP_H = 1920, 1080         # requested capture size; the LIVE frame dims (frame.shape)
                                  # are authoritative for coord back-projection (see loop)

# Model key names are normalized to the Pico's vocabulary inside r4_client
# (norm_key), so no numeric keymap is needed here anymore.


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


def do_action(r4, inp):
    a = inp.get("action")
    coord = inp.get("coordinate")
    x = y = None
    if coord:
        x, y = int(coord[0]), int(coord[1])     # already target-space

    if a in ("finished",):       return "finished"
    if a == "screenshot":        return "screenshot"
    if a == "wait":
        time.sleep(float(inp.get("duration", 1))); return "wait"
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
    if a == "left_click_drag":
        r4.down(); r4.move(x, y); r4.up(); return f"drag {x},{y}"
    if a == "type":
        r4.type(inp.get("text", "")); return f"type {inp.get('text','')!r}"
    if a == "key":
        t = inp.get("text", "")
        if "+" in t:
            r4.combo(t); return f"combo {t}"
        if t:
            r4.key(t); return f"key {t}"
        return "skip-key"
    if a == "scroll":
        if coord:
            r4.move(x, y)
        d = inp.get("scroll_direction", "down")
        amt = int(inp.get("scroll_amount", 3))
        r4.scroll(amt if d == "up" else -amt); return f"scroll {d} {amt}"
    return f"skip-{a}"


def preview(frame, inp):
    f = frame.copy()
    coord = inp.get("coordinate")
    if coord:
        x, y = int(coord[0]), int(coord[1])
        cv2.circle(f, (x, y), 24, (0, 0, 255), 3)
        cv2.line(f, (x - 36, y), (x + 36, y), (0, 0, 255), 2)
        cv2.line(f, (x, y - 36), (x, y + 36), (0, 0, 255), 2)
    cv2.imshow("capture", f); cv2.waitKey(1)
    print(f"  PENDING: {inp}")
    print("  check preview, Enter to run (Ctrl-C aborts)")
    input()


def main():
    print(f"MODEL: {MODEL_NAME} (local)   GOAL: {GOAL}\n")
    log = RunLogger(f"evocua:{MODEL_NAME}", GOAL)
    r4 = R4()
    print("R4 connected")

    cam = Camera(CAM_INDEX)
    t0 = time.time()
    while cam.read() is None:
        if time.time() - t0 > 5:
            raise SystemExit("no frames — OBS holding the card?")
        time.sleep(0.05)
    print("capture open")
    f0 = cam.read()
    print(f"capture dims: {f0.shape[1]}x{f0.shape[0]}  "
          f"(Pico SCREEN_W/SCREEN_H in code.py MUST equal these for accurate clicks)")

    client = OpenAI(base_url=MODEL_URL, api_key="ollama")
    screenshots = []          # processed-image b64 per step (current appended each iter)
    responses   = []          # raw model responses per completed step (for history replay)
    history     = []          # mechanical action descriptions (logging only)
    acted = 0
    finished = False

    try:
        for it in range(MAX_ITERS):
            frame = cam.read()
            fh, fw = frame.shape[:2]          # live capture dims = the target coord space
            # EvoCUA-style preprocessing: smart_resize + get processed dims
            b64, pw, ph = evocua.process_image(frame)
            screenshots.append(b64)           # current frame is the last element

            t_api = time.time()
            raw = evocua.ground(client, MODEL_NAME, screenshots, responses, pw, ph, GOAL)
            responses.append(raw)             # record for next step's history replay
            latency = time.time() - t_api
            print(f"[{it}] ({latency:.1f}s) raw: {raw.strip()[:140]}")

            inp = evocua.parse_action(raw, pw, ph, fw, fh)
            if not inp:
                log.step(it, raw, ["unparsed"], None, None, latency, frame)
                print("  [skip] unparsed — check the raw output format"); continue

            if inp["action"] == "finished":
                finished = True
                log.step(it, raw, ["finished"], None, None, latency, frame)
                print("EvoCUA: terminate."); break

            if acted < CONFIRM_FIRST:
                preview(frame, inp)
            desc = do_action(r4, inp)
            acted += 1
            history.append(desc)
            print(f"[{it}] did: {desc}")

            pc = inp.get("coordinate")
            settle_and_show(cam, SETTLE_SEC)
            log.step(it, raw, [desc], pc, None, latency, cam.read())
        else:
            print("\nHit MAX_ITERS cap.")
    finally:
        log.finish(finished)
        cam.release(); r4.close(); cv2.destroyAllWindows()
        print("released")


if __name__ == "__main__":
    main()
