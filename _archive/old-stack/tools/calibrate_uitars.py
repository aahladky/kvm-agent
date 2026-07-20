"""
calibrate_uitars.py — coordinate calibration for the UI-TARS adapter.

WHAT IT CHECKS (one variable): does a UI-TARS grounding click, AFTER the adapter's
coordinate conversion, land on the target ON THE REAL SCREEN? This isolates the
COORDINATE CONVENTION (does our smart_resize assumption match what Ollama actually feeds
the model) from the model's grounding *quality*:
  * A CONVENTION bug shows up as a SYSTEMATIC offset even on EASY targets — and is most
    visible at the SCREEN EDGES/CORNERS (a scaling error vanishes at center, blows up at
    the edge). So calibrate on corner/edge targets: Start button, clock, a desktop icon.
  * Grounding wobble shows up as occasional misses on small/ambiguous targets — not what
    we're testing here.

SAFE BY DEFAULT: only MOVES the Pico cursor (no click), so there are zero side effects —
you just watch whether the physical cursor lands on the thing you named. It also saves a
red-crosshair overlay on the captured frame (calib_frames/) so you can see the predicted
point. Use --click to also click.

If the cursor is consistently offset (especially worse toward the edges), the smart_resize
budget the adapter assumes differs from Ollama's. Re-probe with --min-pixels/--max-pixels
to find the match, then bake those into UITARSAgent.

Usage (on the laptop; capture card + Pico live; only ONE process may own the rig):
    python calibrate_uitars.py "the Start button in the bottom-left"
    python calibrate_uitars.py "the Start button" "the clock in the bottom-right" "the Recycle Bin"
    python calibrate_uitars.py --click "the Start button"      # also click
    python calibrate_uitars.py                                  # interactive: prompt for targets
    python calibrate_uitars.py --max-pixels 4000000 "the clock" # A/B the smart_resize budget
"""
import os
import re
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

from io import BytesIO                       # noqa: E402
from PIL import Image, ImageDraw             # noqa: E402
from uitars_agent import UITARSAgent         # noqa: E402
from pico_env import PicoEnv                 # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib_frames")


def first_xy(actions):
    """Pull (x, y) out of the first click/move action string the adapter produced."""
    for a in actions:
        m = re.search(r"\((\d+),\s*(\d+)\)", a)
        if m:
            return int(m.group(1)), int(m.group(2)), a
    return None


def main():
    ap = argparse.ArgumentParser(description="UI-TARS coordinate calibration probe.")
    ap.add_argument("targets", nargs="*", help="things to locate, e.g. 'the Start button'")
    ap.add_argument("--model", default="uitars-q4")
    ap.add_argument("--click", action="store_true", help="also click (default: move cursor only)")
    ap.add_argument("--min-pixels", type=int, default=802816)    # match served mmproj (ollama load log)
    ap.add_argument("--max-pixels", type=int, default=3211264)   # image_min_pixels / image_max_pixels
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    # single-shot grounding: no history, reset before each probe
    agent = UITARSAgent(model=args.model, max_history_turns=0,
                        min_pixels=args.min_pixels, max_pixels=args.max_pixels)
    env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
    print(f"[calib] model={args.model}  move_only={not args.click}  "
          f"min_px={args.min_pixels} max_px={args.max_pixels}")

    def probe(target, i):
        agent.reset()
        obs = env.observe()                        # capture only; NO physical action
        W, H = Image.open(BytesIO(obs["screenshot"])).size
        text, actions = agent.predict(f"Click {target}.", obs)
        xy = first_xy(actions)
        print(f"\n[{i}] target: {target!r}")
        print(f"    capture {W}x{H}   model: {(text or '').strip()[:140]!r}")
        if not xy:
            print(f"    !! no click parsed -> actions={actions}")
            return
        x, y, raw = xy
        im = Image.open(BytesIO(obs["screenshot"])).convert("RGB")
        d = ImageDraw.Draw(im)
        d.line([x - 45, y, x + 45, y], fill=(255, 0, 0), width=3)
        d.line([x, y - 45, x, y + 45], fill=(255, 0, 0), width=3)
        d.ellipse([x - 13, y - 13, x + 13, y + 13], outline=(255, 0, 0), width=3)
        path = os.path.join(OUT, f"calib_{i:02d}.png")
        im.save(path)
        env.r4.move(x, y)                          # move the real cursor so you can SEE it land
        if args.click:
            time.sleep(0.3)
            env.r4.click()
        print(f"    -> ({x},{y})  [{x/W:.1%},{y/H:.1%} of screen]   crosshair: {path}")
        print(f"    cursor moved{' + clicked' if args.click else ' (no click)'} — on target?")

    try:
        if args.targets:
            for i, t in enumerate(args.targets):
                probe(t, i)
        else:
            i = 0
            while True:
                try:
                    t = input("\ntarget> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if t.lower() in ("", "q", "quit", "exit"):
                    break
                probe(t, i)
                i += 1
    finally:
        env.close()
        print("hardware released.")


if __name__ == "__main__":
    main()
