"""
test_target.py — OFFLINE test for the manual power/reset seam.

    python tests/test_target.py
"""
import sys, os, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.hardware import target


def test_reboot_operator_confirmation():
    calls = []
    real_input = builtins.input
    builtins.input = lambda prompt="": calls.append(prompt) or ""
    try:
        target.reboot()
    finally:
        builtins.input = real_input
    assert len(calls) == 1, "reboot() blocks on operator confirmation exactly once"
    assert "power-cycle" in calls[0].lower(), "reboot() prompt tells the operator what to do"
    assert target.is_up() is True, "is_up() is True after operator confirmation (v1 contract)"


# --- verify_hid gate (fake r4 + fake cam; verifies the camera-checked round-trips) ---
import cv2
import numpy as np


def _png(v):
    arr = np.full((270, 480, 3), v, np.uint8)
    ok, buf = cv2.imencode(".png", arr)
    return buf.tobytes()


SAME, CHANGED = _png(128), _png(255)   # tile-max diff 127 >> thresh=20


class FakeR4:
    def __init__(self):
        self.calls = []
    def combo(self, s): self.calls.append(("combo", s))
    def key(self, k): self.calls.append(("key", k))
    def type(self, text): self.calls.append(("type", text))
    def move(self, x, y): self.calls.append(("move", x, y))
    def click(self): self.calls.append(("click",))


def test_reset_manifest_rejects_paths_globs_and_unknown_settings():
    for unsafe in ("../hello.txt", "/tmp/x", "subdir/file", "*.txt", "$HOME", "..", ""):
        try:
            target.validate_reset_manifest([unsafe], [])
        except ValueError:
            continue
        raise AssertionError(f"unsafe cleanup target {unsafe!r} must be rejected")
    try:
        target.validate_reset_manifest([], ["arbitrary-shell"])
    except ValueError:
        pass
    else:
        raise AssertionError("task JSON cannot invent a settings command")
    try:
        target.validate_reset_manifest([], [], "arbitrary-apps")
    except ValueError:
        pass
    else:
        raise AssertionError("task JSON cannot invent process-kill commands")


def test_gnome_reset_command_is_narrow_and_fail_loud():
    cmd = target.build_gnome_reset_command(
        ["hello.txt", "notes.txt"], ["default-color-scheme"])
    assert 'rm -f -- "$HOME/hello.txt" "$HOME/notes.txt"' in cmd
    assert "gsettings reset org.gnome.desktop.interface color-scheme" in cmd
    assert cmd.endswith("exit || echo KVM_RESET_FAILED")
    assert "rm -rf" not in cmd and "$HOME/*" not in cmd
    assert "gnome-text-editor" in cmd and "gnome-control-center" in cmd
    assert "firefox" in cmd and "Pinta.exe" in cmd
    assert "gnome-terminal-server" in cmd and "kgx" in cmd and "ptyxis" in cmd
    assert "pkill -KILL -i" in cmd and "pkill -TERM" not in cmd
    assert "gnome-session-quit" not in cmd


def test_gnome_reset_is_typed_through_physical_hid():
    r4 = FakeR4()
    command = target.reset_gnome_session(r4, ["report.txt"], settle_s=0)
    assert r4.calls == [
        ("combo", "ctrl+alt+t"),
        ("type", command),
        ("key", "enter"),
    ]


class FakeCam:
    """png_bytes() pops a scripted sequence; read() returns a constant frame so
    wait_until_stable sees instant stability."""
    def __init__(self, frames):
        self.frames = list(frames)
        self._still = np.zeros((270, 480, 3), np.uint8)
    def png_bytes(self, full_res=False):
        return self.frames.pop(0) if self.frames else SAME
    def read(self):
        return self._still


def test_verify_hid_gate_gnome_default():
    """GNOME landmarks (the current Ubuntu target): keyboard round-trip is a Super
    tap (opens Activities, Esc closes), mouse round-trip clicks the Activities
    corner (top-left)."""
    r4 = FakeR4()
    cam = FakeCam([SAME, CHANGED, SAME, CHANGED])
    ok, detail = target.verify_hid(r4, cam, screen=(1920, 1080))
    assert ok is True, "gate passes when both round-trips show change"
    assert ("key", "win") in r4.calls and ("key", "esc") in r4.calls, \
        "keyboard round-trip is a Super tap (+ esc), OS-portable"
    moves = [c for c in r4.calls if c[0] == "move"]
    assert moves and moves[0][2] < 40 and moves[0][1] < 60, \
        f"mouse round-trip clicks the TOP-left Activities corner, got {moves}"
    assert ("click",) in r4.calls, "mouse round-trip clicks"


def test_verify_hid_gate_windows_shell():
    """The Windows landmarks remain available for a Windows target."""
    r4 = FakeR4()
    cam = FakeCam([SAME, CHANGED, SAME, CHANGED])
    ok, detail = target.verify_hid(r4, cam, screen=(1920, 1080), shell="windows")
    assert ok is True, "windows-shell gate passes when both round-trips show change"
    assert ("combo", "win+r") in r4.calls and ("key", "esc") in r4.calls, \
        "windows keyboard round-trip (win+r + esc)"
    assert ("move", 20, 1055) in r4.calls and ("click",) in r4.calls, \
        "windows mouse round-trip (Start click)"


def test_verify_hid_gate_fail_closed():
    r4_dead = FakeR4()
    cam_dead = FakeCam([SAME, SAME, SAME, SAME])
    ok_dead, detail_dead = target.verify_hid(r4_dead, cam_dead)
    assert ok_dead is False, "gate fails closed when nothing changes"
    assert "keyboard" in detail_dead, "gate names the dead collection (keyboard first)"

    r4_mdead = FakeR4()
    cam_mdead = FakeCam([SAME, CHANGED, SAME, SAME])
    ok_mdead, detail_mdead = target.verify_hid(r4_mdead, cam_mdead)
    assert ok_mdead is False and "mouse" in detail_mdead, \
        "gate catches the half-dead case (mouse only)"


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
