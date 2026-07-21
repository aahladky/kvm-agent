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


def test_windows_gui_key_aliases_resolve():
    for alias in ("win", "gui", "cmd", "leftgui", "winkey", "windows", "super", "meta"):
        assert KEYCODES.get(alias) == 0xE3, f"{alias!r} resolves to the GUI keycode"


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
