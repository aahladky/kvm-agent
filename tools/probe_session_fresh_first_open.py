"""
probe_session_fresh_first_open.py -- the faithful version: does a genuine VM
snapshot-revert + cold reboot between trials reproduce the WinUI3 flyout first-open
bug? (See tools/probe_first_open_reliability.py for the cheaper process-relaunch-only
probe that came first and found 6/6 success -- it turned out to test the wrong level
of "fresh": the Windows SESSION was already warm from earlier trials that day, even
though each Notepad process was new.)

Uses the exact revert_clean() machinery waa/runner.py calls before every real task
(kvm_agent/hardware/vm.py) -- a truly fresh session, not just a fresh process -- then
tests the very first flyout interaction (File menu -> Save as) immediately after each
fresh boot.

Result on the rig 2026-07-19: 4/4 trials FAILED -- File menu opened cleanly every
time, but the first-ever click on "Save as" inside it never produced the dialog. This
was the finding that confirmed the WinUI3 MenuFlyout first-open bug
(microsoft/microsoft-ui-xaml#10481) is real and near-deterministic on THIS rig, not
occasional noise -- and since every real WAA task starts from a cold VM revert, every
task's first flyout use hits this. See var/scratch/2026-07-19_flyout_click_investigation/
for the original run's screenshots.

Costs real time: ~35-45s/trial for the VM revert+reboot alone. Default N=4 keeps total
runtime reasonable; raise it for a more solid sample if you have time to spare.

    python tools/probe_session_fresh_first_open.py
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware.env import PicoEnv, wait_until_stable
from kvm_agent.hardware.vm import VMController

FILE_MENU = (30, 59)
SAVE_AS = (48, 291)
N = 4


def main():
    tag = time.strftime("probe_session_fresh_first_open_%Y%m%d_%H%M%S")
    out = os.path.join(CFG.runs_dir, tag)
    os.makedirs(out, exist_ok=True)

    env = PicoEnv(screen_size=(1280, 720))
    r4 = env.r4
    vm = VMController()

    def snap(name):
        import cv2
        f = env.cam.read()
        cv2.imwrite(os.path.join(out, name + ".png"), f)

    def settle(max_s=2.5):
        wait_until_stable(env.cam.read, max_s)

    results = []
    for i in range(N):
        trial = f"{i:02d}"
        print(f"\n[trial {trial}] === VM revert + cold reboot (genuine fresh session) ===")
        vm.revert_clean(capture_fn=None, check_hid=True, cold_boot=True)
        print(f"[trial {trial}] desktop ready, launching Notepad (first app interaction this session)...")

        r4.combo("win+r")
        time.sleep(0.8)
        r4.type("notepad")
        r4.key("enter")
        time.sleep(2.0)
        settle()
        snap(f"{trial}_00_fresh_notepad")

        print(f"[trial {trial}] first-ever File menu click (first WinUI3 flyout this session)...")
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
        print(f"[trial {trial}] done.")

    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nAll trials complete -> {out}")
    env.cam.release()


if __name__ == "__main__":
    main()
