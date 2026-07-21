"""
test_key_aliases.py — OFFLINE test: the model's common names for the Windows/GUI
key all resolve to the same keycode (2026-07-21: 'winkey' from the model 502'd a
battery run at step 1 — unknown_key crashed the task before aliases existed).

    python tests/test_key_aliases.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "appliance", "pi5"))

from pikvm_proto import KEYCODES

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

for alias in ("win", "gui", "cmd", "leftgui", "winkey", "windows", "super", "meta"):
    check(f"{alias!r} resolves to the GUI keycode", KEYCODES.get(alias) == 0xE3)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
