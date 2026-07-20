"""
pull_pi5_logs.py -- pull the Pi 5 appliance's own wire-level HID command log down to
CFG.logs_dir. The Pi 5 (192.168.0.29, hid-bridge.service) writes its own copy of every
HID command's wire-level response (decoded Pico ACK, target kbd/mouse-online state) to
/home/aaron/hid_bridge_commands.jsonl on the Pi 5 itself -- see docs/EXTERNAL_DEPS.md.
Manual/on-demand, not scheduled; run it whenever you need to cross-reference the
appliance-side log against the host-side CFG.logs_dir/appliance_client_commands.jsonl.

    python3 tools/pull_pi5_logs.py
    python3 tools/pull_pi5_logs.py --host 192.168.0.29 --remote-path /home/aaron/hid_bridge_commands.jsonl
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="aaron@192.168.0.29")
    ap.add_argument("--remote-path", default="/home/aaron/hid_bridge_commands.jsonl")
    ap.add_argument("--out", default=None,
                     help="local destination; default CFG.logs_dir/pi5_hid_bridge_commands.jsonl")
    args = ap.parse_args()

    os.makedirs(CFG.logs_dir, exist_ok=True)
    out = args.out or os.path.join(CFG.logs_dir, "pi5_hid_bridge_commands.jsonl")

    cmd = ["scp", f"{args.host}:{args.remote_path}", out]
    print(f"[pull_pi5_logs] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"scp failed (exit {result.returncode})")

    size = os.path.getsize(out)
    print(f"[pull_pi5_logs] pulled {size} bytes -> {out} at {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
