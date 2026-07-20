"""
isolate_default_browser.py — ISOLATION HARNESS for "set default browser to Chrome" (Windows 10).

WHY THIS EXISTS (Aaron's "don't stack unknowns"): run a DETERMINISTIC, known-good action
sequence for this ONE task so we can answer ONE question cleanly:

    With the CORRECT steps, can the executive + UI-TARS grounding + the rig actually
    COMPLETE this task — or is a primitive/feedback gap blocking it regardless of plan?

  - If it PASSES  -> the failures in runs/goal_*.json were the PLAN + the broken verify,
                     not the executor. The fix is Tier-1 (truthful verify + a real click
                     success signal) + a Win10 default-apps idiom in the planner. A bigger
                     planner is NOT the lever.
  - If a specific grounded CLICK misses -> we've isolated grounding on that exact target;
                     the per-step frames + red crosshairs show which one and by how much.
  - If keyboard NAV can't reach "Web browser" -> we've isolated the reach/scroll gap.

WHEEL-FREE BY DESIGN. The live Pico firmware (code.py:265) maps the scroll command 'S' to a
no-op (`move_to(_cur_x,_cur_y)  # scroll removed for now`), and the boot.py v4 HID report is
5 bytes [buttons,xL,xH,yL,yH] with NO wheel field. So r4.scroll() physically cannot scroll the
target. On Win10, "Web browser" is the LAST item in Default apps and is often below the fold —
the likely real reason the live runs misgrounded at the screen edge ([23,1058]). This harness
therefore reaches the control with KEYBOARD ONLY (maximize the window, or Tab focus which
auto-scrolls), so you can run it TODAY without reflashing firmware.

WHAT THIS DOES THAT run_plan DOESN'T (it previews the proposed Tier-1 fixes):
  - saves a frame after EVERY step (run_plan saves only the final JSON, so you can't see WHY
    a click missed);
  - draws a red crosshair at each grounded coordinate (eyeball grounding precision);
  - uses a VISION yes/no confirm as the click success signal instead of frame-diff
    ("pixels moved" -> false positives on a misground);
  - VERIFIES by READING STATE with the vision model ("is Chrome the shown default?") instead
    of the impossible substring match the planner emitted
    (expect="Google Chrome is now the default web browser" — a sentence never on screen);
  - launches by URI (ms-settings:defaultapps), not the unreliable Win+R "settings".

WINDOWS 10 FLOW (NOT Win11 — there is NO search box; the defaults list is static tiles):
  Win+R ms-settings:defaultapps -> maximize -> (reach "Web browser" by keyboard) ->
  click the current browser tile -> pick "Google Chrome" in the flyout ->
  dismiss the "try Microsoft Edge" nag if it appears -> verify Chrome is the shown default.

RUN (from the repo root, on the desktop that holds the camera + Pico):
    python tools/isolate_default_browser.py                 # default: maximize + grounded picks
    python tools/isolate_default_browser.py --reach tab     # pure-keyboard reach (A/B vs maximize)
    python tools/isolate_default_browser.py --flyout keyboard   # pick Chrome by keys, not a click
    python tools/isolate_default_browser.py --no-reset      # skip the clean-desktop sweep
Outputs frames + summary.json to runs/isolate_defbrowser_<timestamp>/.

NOTE: I can't run this from here — the camera/Pico/Ollama are on your LAN. Every step below is
from static reading of the live code; re-verify on the rig.
"""
import os
import re
import sys
import json
import time
import argparse

# repo root on the path so `python tools/this.py` finds the kvm_agent package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

from kvm_agent.config import CFG

from kvm_agent.orchestration.executive import Executive, Verifier


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reach", choices=("maximize", "tab"), default="maximize",
                    help="how to bring 'Web browser' into view without a scroll wheel")
    ap.add_argument("--tabs", type=int, default=8,
                    help="(reach=tab) how many Tabs to reach the Web browser tile")
    ap.add_argument("--flyout", choices=("click", "keyboard"), default="click",
                    help="pick Chrome in the chooser by grounded click or by keys")
    ap.add_argument("--tile-desc",
                    default="the current default web browser button under the 'Web browser' heading",
                    help="grounding target for the current-default browser tile (A/B phrasing here)")
    ap.add_argument("--chrome-desc",
                    default="the 'Google Chrome' item in the open app chooser list",
                    help="grounding target for Chrome in the chooser")
    ap.add_argument("--no-reset", action="store_true",
                    help="skip the reset-to-clean-desktop sweep")
    ap.add_argument("--executor", default="uitars-q4")
    ap.add_argument("--vision", default="qwen2.5vl:7b")
    ap.add_argument("--runs-dir", default=CFG.runs_dir)
    args = ap.parse_args()

    tag = time.strftime("isolate_defbrowser_%Y%m%d_%H%M%S")
    out = os.path.join(args.runs_dir, tag)
    os.makedirs(out, exist_ok=True)
    log = []
    n = [0]  # mutable step counter for ordered frame filenames

    ex = Executive.open(executor_model=args.executor, verifier=Verifier(vision_model=args.vision))

    # ---- helpers -----------------------------------------------------------
    def snap(name, xy=None):
        """Save the current frame; if xy given, draw a red crosshair to eyeball grounding."""
        png = ex.observe()
        n[0] += 1
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if xy is not None:
            x, y = int(xy[0]), int(xy[1])
            cv2.drawMarker(arr, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 46, 3)
            cv2.circle(arr, (x, y), 26, (0, 0, 255), 2)
        cv2.imwrite(os.path.join(out, f"{n[0]:02d}_{name}.png"), arr)
        return png

    def vsay(question):
        """Raw vision answer (string) for richer logging than a bare bool."""
        return ex.verifier._vision(ex.observe(), question + " Answer only 'yes' or 'no'.") or ""

    def vyes(question):
        return "yes" in vsay(question).lower()

    def step(name, ok, **extra):
        rec = {"step": name, "ok": bool(ok)}
        rec.update(extra)
        log.append(rec)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))
        return ok

    def wait_yes(question, timeout=14.0, poll=2.0):
        """Poll vision until 'yes' or timeout — previews wait-for-stable vs a fixed sleep."""
        t0 = time.time()
        ans = ""
        while time.time() - t0 < timeout:
            ans = vsay(question)
            if "yes" in ans.lower():
                return True
            time.sleep(poll)
        return "yes" in ans.lower()

    def ground_click(desc, confirm_q, settle=1.8):
        """Ground -> click -> snap(with crosshair) -> vision-confirm the post-state.
        Returns (xy, confirmed). `confirmed` is the REAL success signal (vs frame-diff)."""
        xy, action = ex.ground(desc)
        if xy is None:
            snap(f"GROUNDFAIL_{_slug(desc)}")
            return None, False
        ex.env.r4.move(*xy); ex.env.r4.click(); time.sleep(settle)
        snap(f"click_{_slug(desc)}", xy=xy)
        return xy, vyes(confirm_q)

    # ---- the sequence ------------------------------------------------------
    try:
        print(f"[isolate] target=Windows10  reach={args.reach}  flyout={args.flyout}\n  out={out}")

        # 0) clean desktop (optional)
        if not args.no_reset:
            r = ex.reset_clean(max_close=12)
            snap("reset")
            step("reset_clean", r.get("cleared", False), detail=r)

        # 1) open Default apps directly by URI (NOT Win+R 'settings')
        ex.env.r4.combo("win+r"); time.sleep(1.2)
        ex.env.r4.type("ms-settings:defaultapps"); time.sleep(0.4)
        ex.env.r4.key("enter")
        opened = wait_yes("Is the Windows Settings 'Default apps' page open and visible?")
        snap("open_defaultapps")
        if not step("open ms-settings:defaultapps", opened):
            raise SystemExit("Default apps page did not open — fix launch before anything else.")

        # 2) maximize so more of the (un-scrollable) list is visible
        ex.env.r4.combo("win+up"); time.sleep(1.2)
        snap("maximize")

        # 3) reach 'Web browser' WITHOUT a scroll wheel
        web_visible = vyes("Is a 'Web browser' heading or label visible on this page?")
        if not web_visible and args.reach == "tab":
            # Tab focus auto-scrolls the focused control into view (wheel-free).
            for k in range(args.tabs):
                ex.env.r4.key("tab"); time.sleep(0.25)
            snap("tab_to_webbrowser")
            web_visible = vyes("Is a 'Web browser' heading or label visible on this page?")
        step(f"reach 'Web browser' ({args.reach})", web_visible)

        # 4) open the chooser flyout for the current default browser tile
        if args.reach == "tab" and web_visible:
            # tile already focused by the Tab walk -> Space/Enter opens the chooser, no grounding
            ex.env.r4.key("space"); time.sleep(1.4)
            snap("open_chooser_keyboard")
            flyout = vyes("Is a small pop-up list of browser apps (a 'Choose an app' chooser) open?")
            tile_xy = None
        else:
            tile_xy, flyout = ground_click(
                args.tile_desc,
                "Is a small pop-up list of browser apps (a 'Choose an app' chooser) now open?")
        step("open browser chooser", flyout, tile_xy=tile_xy)

        # 5) pick Google Chrome
        if args.flyout == "keyboard":
            # in the chooser listbox, jump by first letter then commit
            ex.env.r4.key("g"); time.sleep(0.4)
            ex.env.r4.key("enter"); time.sleep(1.6)
            snap("pick_chrome_keyboard")
            chrome_xy = None
            picked = None  # keyboard mode emits no coord; the VERIFY step is the real check
        else:
            chrome_xy, picked = ground_click(
                args.chrome_desc,
                "Has the chooser closed and 'Google Chrome' been selected?")
        time.sleep(1.0)
        step("pick Google Chrome", picked is not False, chrome_xy=chrome_xy, confirmed=picked)

        # 6) dismiss the Win10 'Before you switch / try Microsoft Edge' nag if present
        if vyes("Is there a dialog trying to keep you on, or recommend, Microsoft Edge?"):
            nxy, closed = ground_click("the 'Switch anyway' button",
                                       "Did the Microsoft Edge dialog close?")
            step("dismiss Edge nag", closed, nag_seen=True, xy=nxy)
        else:
            step("Edge nag", True, nag_seen=False)

        # 7) TRUTHFUL verify — read STATE, not an impossible literal string
        time.sleep(1.0)
        verify_q = ("On this Windows Default apps page, under the 'Web browser' heading, is "
                    "Google Chrome shown as the current default web browser?")
        raw = vsay(verify_q)
        verified = "yes" in raw.lower()
        snap("verify")
        step("VERIFY chrome is default", verified, vision_said=raw[:80])

        verdict = "PASS" if verified else "FAIL"
        print(f"\n  ===== {verdict}: Chrome is default = {verified}  (vision: {raw[:60]!r}) =====")
        print(f"  frames + log: {out}")
        return 0 if verified else 1
    finally:
        json.dump({"tag": tag, "target_os": "windows10", "reach": args.reach,
                   "flyout": args.flyout, "log": log},
                  open(os.path.join(out, "summary.json"), "w"), indent=2)
        ex.close()


if __name__ == "__main__":
    sys.exit(main())
