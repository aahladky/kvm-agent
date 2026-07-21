"""
target.py — physical-target power/reset seam
(docs/PLAN_2026-07-20_physical_target_move.md §2).

Replaces the libvirt VMController (archived 2026-07-20 with the VM stack). v1 is
MANUAL: the operator power-cycles the laptop and confirms the desktop is up. The
power-control decision (WoL vs smart plug vs hybrid) is deliberately deferred until
the hardware is in front of us; wol/smartplug backends slot in behind these same two
functions without touching callers (tools/battery.py).
"""


def reboot():
    """Full restart of the physical target between battery tasks. v1: the operator
    does it by hand; their Enter IS the readiness signal (desktop up and settled)."""
    input("[target] Power-cycle the laptop (full shutdown + boot). "
          "Press Enter when the desktop is up and settled... ")


def is_up():
    """v1 contract: True once reboot() returned (the operator confirmed). When a real
    backend lands this becomes an actual readiness probe."""
    return True


def verify_hid(r4, cam, screen=(1920, 1080), thresh=20.0, settle_s=4.0, attempts=2):
    """Functional HID gate: prove the keyboard AND mouse collections actually deliver
    to the target OS, camera-verified. The firmware's probe flags can LIE -- 2026-07-21:
    a post-reboot half-dead composite device reported mouse_online=true while every
    click vanished between the laptop's USB host and Windows (the I2 class, physical
    edition; keyboard on the same device worked). The camera is the only truth.

    Keyboard round-trip: Win+R must open the Run dialog; Esc closes it.
    Mouse round-trip: a Start-button click must open the Start menu; Esc closes it.
    thresh=20.0 sits far above taskbar widget churn (~5-12) and far below a real
    dialog/menu appearing (measured 60-200+ on the laptop).

    Contamination-hardened (same day, second bug): a leftover OPEN Run dialog makes
    win+r a no-op (diff ~0) and an UNFOCUSED one ignores Esc -- reading falsely as
    "keyboard dead". Each round-trip therefore runs up to `attempts` times: pre-Esc to
    dismiss leftovers, and a small diff triggers a clean retry (the first attempt's
    post-Esc closes a refocused leftover, so the retry measures a real open). Returns
    (ok: bool, detail: str)."""
    # lazy: keeps target.py importable without cv2/numpy (env.py drags both in).
    # 2026-07-21: was `from agent_loop_holo import _frame_diff_score` -- a package ->
    # app-script inversion (review P3); the metric's canonical home is env.py now.
    from kvm_agent.hardware.env import frame_diff_score, wait_until_stable

    class _CaptureDead(Exception):
        """No frames during the verify window: the CAMERA is the dead component --
        without it a dead-still diff would misread as 'HID not delivering' and send
        the operator replugging the wrong device (review 2026-07-21 P0-5)."""

    def round_trip(fire):
        """esc -> settle -> before -> fire() -> settle -> diff -> esc -> settle.
        Returns the diff of the first attempt that beats thresh, else the last diff."""
        diff = 0.0
        for _ in range(attempts):
            r4.key("esc")
            wait_until_stable(cam.read, 1.0)
            before = cam.png_bytes()
            fire()
            if wait_until_stable(cam.read, settle_s) == "no_frames":
                raise _CaptureDead()
            diff = frame_diff_score(before, cam.png_bytes())
            r4.key("esc")
            wait_until_stable(cam.read, 1.0)
            if diff > thresh:
                break
        return diff

    try:
        kbd_diff = round_trip(lambda: r4.combo("win+r"))
        if kbd_diff <= thresh:
            return False, f"keyboard NOT delivering (win+r diff {kbd_diff:.1f} <= {thresh})"

        def start_click():
            r4.move(20, screen[1] - 25)   # Start button, bottom-left
            r4.click()

        mouse_diff = round_trip(start_click)
        if mouse_diff <= thresh:
            return False, f"mouse NOT delivering (Start click diff {mouse_diff:.1f} <= {thresh})"
        return True, f"hid ok (kbd diff {kbd_diff:.1f}, mouse diff {mouse_diff:.1f})"
    except _CaptureDead:
        return False, ("capture delivered NO frames during verify -- the camera is the "
                       "dead component here; fix capture before blaming the HID")
