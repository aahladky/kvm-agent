"""
probe_grounding.py — OFFLINE grounding A/B on a SAVED frame (no rig; only laptop Ollama).

Isolates ONE variable. The isolation run (runs/isolate_defbrowser_*/) showed ex.ground()
returned None for the browser tile even though the 'Microsoft Edge' tile is clearly visible in
03_maximize.png — so the failure is in the GROUND step (UI-TARS emitted no coordinate), NOT
launch/reach/verify. This replays a saved frame through UI-TARS with several target phrasings and
prints the raw Thought/Action + parsed xy, so we learn WHICH of these it is:

  - raw text empty / no 'Action:'     -> model call problem (endpoint, model swap, timeout)
  - Thought present but no click       -> model declined / wrong phrasing (the likely case)
  - click emitted, sane xy             -> the phrasing grounds; executive/planner should use it
  - click emitted, xy way off          -> genuine grounding miss for this target on uitars-q4

Touches ONLY the laptop Ollama (uitars-q4). Camera/Pico are not used, so it's safe to run
anytime and it's reproducible against the fixed frame.

    python tools/probe_grounding.py runs/isolate_defbrowser_20260621_084318/03_maximize.png
    python tools/probe_grounding.py FRAME.png -i "Microsoft Edge" -i "the Edge tile"

Writes a crosshair-marked PNG for every phrasing that produced a coordinate, next to the frame
(FRAME_probe/), so you can eyeball precision.
"""
import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from kvm_agent.config import CFG
from kvm_agent.models.uitars import UITARSAgent

# default A/B set: bare label -> lightly decorated -> the verbose phrasing the executive used
# (reproduces the None) -> positional. Tests the "bare beats decorated" lesson from CLAUDE.md.
DEFAULT_INSTRUCTIONS = [
    "Microsoft Edge",
    "the Microsoft Edge button",
    "click Microsoft Edge under Web browser",
    "the current default web browser button under the 'Web browser' heading",
    "the app icon and name directly below the Web browser heading",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frame", help="path to a saved PNG frame")
    ap.add_argument("-i", "--instruction", action="append", dest="instructions",
                    help="target phrasing to test (repeatable; overrides the defaults)")
    ap.add_argument("--grounding", action="store_true",
                    help="use UI-TARS GROUNDING_DOUBAO (click-only) prompt instead of COMPUTER_USE")
    ap.add_argument("--model", default=CFG.executor_model)
    args = ap.parse_args()

    os.environ.setdefault("OPENAI_BASE_URL", CFG.openai_base)
    os.environ.setdefault("OPENAI_API_KEY", CFG.openai_key)

    png = open(args.frame, "rb").read()
    instrs = args.instructions or DEFAULT_INSTRUCTIONS
    ag = UITARSAgent(model=args.model, temperature=0.0, screen_size=CFG.screen_size,
                     grounding=args.grounding)
    base = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    outdir = os.path.splitext(args.frame)[0] + "_probe"
    os.makedirs(outdir, exist_ok=True)

    print(f"[probe] frame={args.frame}  model={args.model}  grounding={args.grounding}  "
          f"endpoint={os.environ['OPENAI_BASE_URL']}")
    for k, ins in enumerate(instrs):
        ag.reset()
        text, actions = ag.predict(ins, {"screenshot": png})
        xy = None
        for a in actions:
            m = re.search(r"\((\d+),\s*(\d+)\)", a)
            if m:
                xy = (int(m.group(1)), int(m.group(2)))
                break
        th = ""
        if text and "Thought:" in text:
            th = text.split("Thought:")[-1].split("Action:")[0].strip()[:200]
        print(f"\n=== [{k}] {ins!r}")
        print(f"     xy      = {xy}")
        print(f"     actions = {actions}")
        print(f"     thought = {th}")
        print(f"     raw     = {(text or '')[:200]!r}")
        if xy is not None:
            img = base.copy()
            cv2.drawMarker(img, xy, (0, 0, 255), cv2.MARKER_CROSS, 46, 3)
            cv2.circle(img, xy, 26, (0, 0, 255), 2)
            cv2.imwrite(os.path.join(outdir, f"{k:02d}_{re.sub(r'[^a-z0-9]+', '_', ins.lower())[:30]}.png"), img)
    print(f"\n[probe] marked frames (where a coord was emitted): {outdir}")


if __name__ == "__main__":
    main()
