"""
probe_first_open_reliability.py -- does relaunching the notepad.exe PROCESS (not the
Windows session/VM) between trials reproduce the WinUI3 flyout first-open bug?

Written 2026-07-19 as a corrected follow-up to probe_flyout_click_ab.py's 12-trial A/B,
which accidentally ran all 12 trials inside a SINGLE Notepad process -- only trial 0 was
a genuine first-open, trials 1-11 all reused an already-warmed File-menu control. This
probe kills and relaunches Notepad fresh before every trial, directly testing "does
first-open reliably fail" rather than "does teleport vs settle matter once warm."

Caveat (real, not resolved by this probe): this resets the notepad.exe PROCESS, not the
full Windows session/VM. Result on the rig that day: 6/6 fresh-process trials
SUCCEEDED -- meaning process-level freshness does NOT reproduce the bug; the session
(DWM/compositor) was already warm from earlier trials. See
tools/probe_session_fresh_first_open.py for the corrected, session-fresh version (a
genuine VM revert+reboot per trial) that DID reproduce it 4/4.

    python tools/probe_first_open_reliability.py
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
N = 6


def main():
    tag = time.strftime("probe_first_open_reliability_%Y%m%d_%H%M%S")
    out = os.path.join(CFG.runs_dir, tag)
    os.makedirs(out, exist_ok=True)

    env = PicoEnv(screen_size=(1280, 720))
    r4 = env.r4

    def snap(name):
        import cv2
        f = env.cam.read()
        cv2.imwrite(os.path.join(out, name + ".png"), f)

    def settle(max_s=2.5):
        wait_until_stable(env.cam.read, max_s)

    def kill_notepad():
        r4.combo("win+r")
        time.sleep(0.8)
        r4.type("taskkill /IM notepad.exe /F")
        r4.key("enter")
        time.sleep(1.2)
        # a console window may flash up; close anything stray with escape, harmless if none
        r4.key("escape")
        time.sleep(0.3)
        settle(1.5)

    def launch_fresh_notepad():
        r4.combo("win+r")
        time.sleep(0.8)
        r4.type("notepad")
        r4.key("enter")
        time.sleep(2.0)
        settle()

    results = []
    for i in range(N):
        trial = f"{i:02d}"
        print(f"[trial {trial}] kill + relaunch notepad (fresh process)...")
        kill_notepad()
        launch_fresh_notepad()
        snap(f"{trial}_00_fresh_notepad")

        print(f"[trial {trial}] first-ever File menu click...")
        r4.move(*FILE_MENU)
        time.sleep(0.25)
        r4.click()
        time.sleep(0.4)
        settle()
        snap(f"{trial}_01_after_file_click")

        print(f"[trial {trial}] first-ever Save-as click (teleport, matches production code)...")
        r4.move(*SAVE_AS)
        r4.click()
        time.sleep(0.5)
        settle()
        snap(f"{trial}_02_after_saveas_click")

        results.append({"trial": trial})
        print(f"[trial {trial}] done.\n")

    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"All trials complete -> {out}")
    print("Inspect *_02_after_saveas_click.png for dialog presence.")
    env.cam.release()


if __name__ == "__main__":
    main()
