"""
_ground_probe.py — characterize EvoCUA grounding on ONE frame across several
known targets. NO HID is sent; this only captures, queries the model, and
draws on a local image. Goal: distinguish a systematic mapping/bias (fixable,
maybe calibratable) from random Q4 noise.

Outputs:
  _ground_frame.png   the raw captured frame (verify image quality / colors)
  _ground_marked.png  same frame with numbered red crosshairs at model coords
and prints the raw coord + raw text for each target.
"""
import time
import cv2
from openai import OpenAI
import evocua

MODEL_URL  = "http://192.168.0.155:11434/v1"
MODEL_NAME = "evocua-8b"
CAM_INDEX  = 0

# Spread across the screen: 1-3 along the bottom dock (x-spread),
# 4-5 higher up (y-spread). All should be visible on the Mac.
TARGETS = [
    "the Apple menu logo at the very TOP-LEFT corner of the screen",
    "the Finder icon (blue smiling face) at the far LEFT of the Dock",
    "the Safari icon (blue compass) in the Dock",
    "the Trash icon at the far RIGHT of the Dock",
    "the clock and date at the TOP-RIGHT of the menu bar",
]

# --- capture one settled frame ---
cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
frame = None
t0 = time.time()
while time.time() - t0 < 1.5:          # let DSHOW warm up, keep latest
    ok, f = cap.read()
    if ok:
        frame = f
cap.release()
if frame is None:
    raise SystemExit("no frame from capture card")

fh, fw = frame.shape[:2]
b64, pw, ph = evocua.process_image(frame)
print(f"frame {fw}x{fh}   processed {pw}x{ph}   (back-proj scale x={fw/pw:.4f} y={fh/ph:.4f})")
cv2.imwrite("_ground_frame.png", frame)

client = OpenAI(base_url=MODEL_URL, api_key="ollama")
vis = frame.copy()
for i, t in enumerate(TARGETS):
    raw = evocua.ground(client, MODEL_NAME, b64, pw, ph, f"Click {t}.", "")
    inp = evocua.parse_action(raw, pw, ph, fw, fh)
    coord = inp.get("coordinate") if inp else None
    snippet = raw.strip().replace("\n", " ")[:90]
    print(f"[{i}] {t}\n      coord -> {coord}    raw: {snippet}")
    if coord:
        x, y = int(coord[0]), int(coord[1])
        cv2.circle(vis, (x, y), 16, (0, 0, 255), 3)
        cv2.line(vis, (x - 26, y), (x + 26, y), (0, 0, 255), 2)
        cv2.line(vis, (x, y - 26), (x, y + 26), (0, 0, 255), 2)
        cv2.putText(vis, str(i), (x + 14, y - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

cv2.imwrite("_ground_marked.png", vis)
print("\nsaved _ground_frame.png and _ground_marked.png")
