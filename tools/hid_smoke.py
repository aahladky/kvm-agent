#!/usr/bin/env python3
"""
hid_smoke.py — first-contact test for a new physical target
(docs/PLAN_2026-07-20_physical_target_move.md §6 step 2).

  1. probes the appliance (BOTH HID collections must report online — the composite
     device can come up half-dead: I2, REPORT_2026-07-19_problems.md)
  2. types a known string via HID into whatever has focus (operator opens Notepad)
  3. saves a full-res evidence frame to runs/hid_smoke_<ts>/
  4. OCRs it with the tesseract CLI if installed and prints what it read

Every actuation/observation layer is exercised; any divergence localizes the fault.

    python tools/hid_smoke.py
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceClient
from kvm_agent.hardware.env import Camera

STRING = "holo smoke 123"


def main():
    run_dir = os.path.join(CFG.runs_dir, f"hid_smoke_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)

    r4 = ApplianceClient()
    r4.clear_hid()   # session starts all-keys-up; also proves the /hid/clear route
    probe = r4.probe()
    ack = str(probe.get("ack"))
    print("[smoke] probe:", ack)
    assert "kbd=1" in ack and "mouse=1" in ack, f"HALF-DEAD HID COLLECTION: {ack}"

    cam = Camera(CFG.cam_index, *CFG.screen_size)
    try:
        input(f"[smoke] open Notepad on the laptop, click inside it, press Enter — "
              f"will type {STRING!r}...")
        r4.type(STRING)
        time.sleep(1.0)
        png = cam.png_bytes(full_res=True)
    finally:
        cam.release()
    frame_path = os.path.join(run_dir, "evidence.png")
    with open(frame_path, "wb") as f:
        f.write(png)
    print(f"[smoke] evidence frame -> {frame_path}")

    tess = shutil.which("tesseract")
    if tess:
        out = subprocess.run([tess, frame_path, "stdout"],
                             capture_output=True, text=True).stdout
        print(f"[smoke] tesseract read: {out.strip()!r} (expected substring {STRING!r})")
    else:
        print("[smoke] tesseract not installed — verify the string on the frame by eye")


if __name__ == "__main__":
    main()
