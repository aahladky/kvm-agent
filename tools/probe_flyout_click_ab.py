"""
probe_flyout_click_ab.py -- A/B: does a settle delay between move() and click() fix
Save-as-in-open-File-menu not activating? Condition A = teleport, immediate click
(current production behavior). Condition B = move, sleep 250ms, click (the
RPA-industry-standard mitigation -- see docs/ for the 2026-07-19 session writeup).

Promoted 2026-07-19 from a throwaway job-tmp probe (var/scratch/
2026-07-19_flyout_click_investigation/ has the original + its sibling drafts +
the evidence/screenshots this run produced). Result on the rig that day: 11/12
trials succeeded regardless of condition -- only the FIRST-EVER flyout open of
the session failed, which pointed at the WinUI3 first-open bug (see
tools/probe_session_fresh_first_open.py) rather than a move/click timing issue.

    python tools/probe_flyout_click_ab.py
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware.env import PicoEnv, wait_until_stable

FILE_MENU = (30, 59)
SAVE_AS = (48, 291)
N_PER_CONDITION = 6


def main():
    tag = time.strftime("probe_flyout_click_ab_%Y%m%d_%H%M%S")
    out = os.path.join(CFG.runs_dir, tag)
    os.makedirs(out, exist_ok=True)

    env = PicoEnv(screen_size=(1280, 720))
    r4 = env.r4

    def snap(name):
        import cv2
        f = env.cam.read()
        cv2.imwrite(os.path.join(out, name + ".png"), f)
        return f

    def settle(max_s=2.0):
        wait_until_stable(env.cam.read, max_s)

    def open_file_menu():
        r4.move(*FILE_MENU)
        time.sleep(0.25)
        r4.click()
        time.sleep(0.4)
        settle()

    def reset_to_baseline():
        # Escape twice: closes a Save-As dialog if one opened, or closes the File menu
        # if the item click failed to activate anything (menu may or may not still be open).
        r4.key("escape")
        time.sleep(0.3)
        r4.key("escape")
        time.sleep(0.3)
        settle()

    results = []
    for i in range(N_PER_CONDITION):
        for cond in ("A_teleport", "B_settle250ms"):
            trial = f"{len(results):02d}_{cond}"
            print(f"[trial {trial}] opening File menu...")
            open_file_menu()
            snap(f"{trial}_pre_open")

            r4.move(*SAVE_AS)
            if cond == "B_settle250ms":
                time.sleep(0.25)
            r4.click()
            time.sleep(0.5)
            settle()
            snap(f"{trial}_post_click")

            results.append({"trial": trial, "cond": cond})
            print(f"  [trial {trial}] done, resetting...")
            reset_to_baseline()

    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nAll trials complete -> {out}")
    print("Inspect *_post_click.png frames for Save-As dialog presence.")
    env.cam.release()


if __name__ == "__main__":
    main()
