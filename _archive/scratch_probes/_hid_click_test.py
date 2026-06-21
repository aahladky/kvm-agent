"""
_hid_click_test.py — isolate the ONE signal: does a button/tip event register?

Run from the desktop (host) against whichever Pico you want to test. Watch the
SCREEN of the machine the Pico is plugged into, and answer each prompt.

Usage:
    python _hid_click_test.py 192.168.0.183     # Pico #1 (Mac)
    python _hid_click_test.py 192.168.0.224     # Pico #2 (Windows)

Each step changes exactly one thing. The goal is a clean yes/no on whether the
v3 digitizer descriptor makes clicks land, with no WiFi/loop/model variables in
the mix.
"""

import sys
from r4_client import R4

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.183"
W, H = 1920, 1080            # set to the target's real resolution if different

r = R4(ip=ip)
print("connected to", ip)

def step(msg, fn):
    input("\n>>> " + msg + "  (Enter to fire)")
    fn()
    print("    sent.")

# 1. Pure positioning — should already work (control).
step("MOVE to center — cursor should jump to middle of screen", lambda: r.move(W//2, H//2))

# 2. Move somewhere clickable, then a single left click (tip).
step("MOVE to (40,40) then LEFT-CLICK — does it select/activate there?",
     lambda: (r.move(40, 40), r.click()))

# 3. Right-click on empty desktop — context menu should appear.
step("MOVE to center then RIGHT-CLICK — context menu should appear",
     lambda: (r.move(W//2, H//2), r.rclick()))

# 4. Drag a rubber-band box on the desktop.
step("DRAG from (300,300) to (800,600) — a selection rectangle should draw",
     lambda: r.drag(300, 300, 800, 600))

print("\nDone. Clicks land = digitizer fix works. Still dead = escalate "
      "(add In-Range toggle / try Finger usage 0x22 / report-protocol).")
r.close()
