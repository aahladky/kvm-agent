"""
test_key_aliases.py — OFFLINE test: the model's common names for the Windows/GUI
key all resolve to the same keycode (2026-07-21: 'winkey' from the model 502'd a
battery run at step 1 — unknown_key crashed the task before aliases existed).
Importable without pyserial since 2026-07-21 (review P2: the hardware dep used to
be load-bearing for this pure table).

    python tests/test_key_aliases.py   (or pytest tests/test_key_aliases.py)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "appliance", "pi5"))

from pikvm_proto import KEYCODES


def test_gui_key_aliases():
    for alias in ("win", "gui", "cmd", "leftgui", "winkey", "windows", "super", "meta"):
        assert KEYCODES.get(alias) == 0xE3, f"{alias!r} must resolve to the GUI keycode"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
