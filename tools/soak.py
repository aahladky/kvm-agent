#!/usr/bin/env python3
"""
soak.py — Phase-0 gate: an unattended soak of the HID appliance + capture pipeline
(docs/ROADMAP.md Phase 0: "an overnight idle-plus-periodic-action soak with zero
silent wedges; every injected fault surfaces loudly"; the plan's Slice B,
_archive/docs_history/PLAN_2026-07-22_roadmap_alignment_slices.md Part 3).

Two check cadences, run until --hours elapses or Ctrl-C:
  - every --probe-interval (default 10s): /hid/probe -- kbd/mouse online,
    watchdog_rebooted, usb_suspended, retries (all now surfaced end-to-end by the
    Phase-0 firmware watchdog/suspend work and the host _roundtrip retry logic --
    see pikvm_proto.py / hid_bridge.py / appliance/pico_fw).
  - every --action-interval (default 5min): a benign corner mouse-move (proves the
    HID path still actuates, not just PONGs) + a camera-liveness check
    (kvm_agent.hardware.env.Camera.wait_newer -- proves capture hasn't silently
    wedged).

Every check result -- pass or fail -- is one JSONL line to runs/soak_<ts>/soak.jsonl
(flushed per line: a crash mid-run must not lose the tail, same discipline as
RunRecorder and CommandLogger elsewhere in this repo).

FAULT INJECTION IS OPERATOR-DRIVEN, NOT SCRIPTED HERE: unplug the UART, restart
hid_bridge, etc. from another terminal/session while this runs. The Phase-0 gate is
"every failure line in the log maps to something the operator actually did" --
correlate by the log's wall-clock timestamps afterward. This script's job is only to
notice and surface failures loudly, never to inject them.

    python tools/soak.py                    # runs until Ctrl-C
    python tools/soak.py --hours 8          # the Phase-0 gate duration
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError
from kvm_agent.hardware.env import Camera

PROBE_INTERVAL_S = 10
ACTION_INTERVAL_S = 5 * 60
CORNER_XY = (10, 10)   # benign: top-left corner, won't disturb whatever's on screen


class SoakLog:
    """Append-only JSONL, flushed per line -- same discipline as RunRecorder/
    CommandLogger elsewhere in this repo (AGENTS.md: make failure loud, keep a
    forensic trail)."""

    def __init__(self, path):
        self.path = path

    def write(self, record):
        record["ts"] = time.time()
        record["t_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["ts"]))
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()


def probe_once(r4, log):
    try:
        result = r4.probe()
        log.write({"check": "probe", "ok": True, "ack": result.get("ack"),
                  "wire": result.get("wire")})
        return True
    except ApplianceError as e:
        log.write({"check": "probe", "ok": False, "error": str(e)})
        print(f"[soak] PROBE FAILED: {e}")
        return False


def action_once(r4, cam, log):
    try:
        seq0 = cam.seq
        result = r4.move(*CORNER_XY)
        cam.wait_newer(seq0, timeout_s=5.0)
        log.write({"check": "action", "ok": True, "wire": result.get("wire")})
        return True
    except Exception as e:
        log.write({"check": "action", "ok": False, "error": str(e)})
        print(f"[soak] ACTION/CAMERA CHECK FAILED: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=None,
                    help="stop after this many hours (default: run until Ctrl-C)")
    ap.add_argument("--probe-interval", type=float, default=PROBE_INTERVAL_S)
    ap.add_argument("--action-interval", type=float, default=ACTION_INTERVAL_S)
    args = ap.parse_args()

    run_dir = os.path.join(CFG.runs_dir, f"soak_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)
    log = SoakLog(os.path.join(run_dir, "soak.jsonl"))
    print(f"[soak] logging to {log.path}")
    print(f"[soak] probe every {args.probe_interval}s, action+camera check every "
         f"{args.action_interval}s"
         + (f", stopping after {args.hours}h" if args.hours else ", until Ctrl-C"))
    print("[soak] fault injection (UART unplug, bridge restart, etc.) is yours to "
         "drive from elsewhere -- every resulting failure should show up here loudly")

    r4 = ApplianceClient()
    cam = Camera(CFG.cam_index, *CFG.screen_size)

    t_start = time.time()
    t_end = t_start + args.hours * 3600 if args.hours else None
    next_probe = t_start
    next_action = t_start
    probes = fails_probe = actions = fails_action = 0
    try:
        while t_end is None or time.time() < t_end:
            now = time.time()
            if now >= next_probe:
                probes += 1
                if not probe_once(r4, log):
                    fails_probe += 1
                next_probe = now + args.probe_interval
            if now >= next_action:
                actions += 1
                if not action_once(r4, cam, log):
                    fails_action += 1
                next_action = now + args.action_interval
            time.sleep(min(1.0, args.probe_interval))
    except KeyboardInterrupt:
        print("\n[soak] stopped by operator")
    finally:
        cam.release()
        summary = {"probes": probes, "fails_probe": fails_probe,
                  "actions": actions, "fails_action": fails_action,
                  "duration_s": round(time.time() - t_start, 1)}
        log.write({"check": "summary", **summary})
        print(f"[soak] {summary}")


if __name__ == "__main__":
    main()
