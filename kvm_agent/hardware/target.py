"""
target.py — physical-target power/reset seam
(_archive/docs_history/PLAN_2026-07-20_physical_target_move.md §2).

Replaces the libvirt VMController (archived 2026-07-20 with the VM stack). v1 is
MANUAL: the operator power-cycles the laptop and confirms the desktop is up. The
power-control decision (WoL vs smart plug vs hybrid) is deliberately deferred until
the hardware is in front of us; wol/smartplug backends slot in behind these same two
functions without touching callers (tools/battery.py).
"""
import re
import shlex
import time

_SAFE_HOME_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Named profiles only: task JSON never gets to provide arbitrary shell.
GNOME_SETTING_RESETS = {
    "default-color-scheme":
        "gsettings reset org.gnome.desktop.interface color-scheme",
}

# Known applications the GNOME battery owns. These are command-line regexes rather
# than executable names because snap applications run through a generic host (Pinta
# is `dotnet ... /snap/pinta/<rev>/.../Pinta.dll`). Bracketing the first character
# keeps each regex from matching the pkill command that carries it. Missing processes
# are normal and ignored.
GNOME_APP_RESETS = {
    "battery-apps": (
        r"(^|/)[g]nome-text-editor([ /]|$)",
        r"(^|/)[g]nome-calculator([ /]|$)",
        r"(^|/)[g]nome-control-center([ /]|$)",
        r"(^|/)[n]autilus([ /]|$)",
        r"(^|/)[p]inta([/. ]|$)",
        r"(^|/)[f]irefox([ /]|$)",
        # Terminal implementations seen across current GNOME distributions. Keep
        # these last: killing the implementation that owns this shell may end command
        # execution immediately, after all files/settings/task apps are already reset.
        r"(^|/)[g]nome-terminal-server([ /]|$)",
        r"(^|/)[g]nome-terminal([ /]|$)",
        r"(^|/)[k]gx([ /]|$)",
        r"(^|/)[g]nome-console([ /]|$)",
        r"(^|/)[p]tyxis([ /]|$)",
        r"(^|/)[x]term([ /]|$)",
    ),
}


def validate_reset_manifest(cleanup_files=(), setting_resets=(),
                            application_reset="battery-apps"):
    """Validate the intentionally tiny target-side mutation vocabulary."""
    files = list(cleanup_files or ())
    settings = list(setting_resets or ())
    for name in files:
        if not isinstance(name, str) or not _SAFE_HOME_FILE.fullmatch(name) or name in (
                ".", ".."):
            raise ValueError(f"unsafe cleanup filename {name!r}: expected one simple "
                             "filename directly under the evaluation user's home")
    unknown = [name for name in settings if name not in GNOME_SETTING_RESETS]
    if unknown:
        raise ValueError(f"unknown GNOME setting reset profile(s): {unknown}")
    if application_reset not in GNOME_APP_RESETS:
        raise ValueError(f"unknown GNOME application reset profile: {application_reset!r}")
    return files, settings, application_reset


def build_gnome_reset_command(cleanup_files=(), setting_resets=(),
                              application_reset="battery-apps"):
    """Build the visible command; failure leaves KVM_RESET_FAILED on screen."""
    files, settings, application_reset = validate_reset_manifest(
        cleanup_files, setting_resets, application_reset)
    commands = []
    if files:
        quoted = " ".join(f'"$HOME/{name}"' for name in files)
        commands.append(f"rm -f -- {quoted}")
    commands.extend(GNOME_SETTING_RESETS[name] for name in settings)
    # One compact shell loop matters over physical HID (~60-90ms per character).
    # The fixed patterns are individually shell-quoted; task JSON can select only a
    # named profile and cannot supply a pattern.
    patterns = " ".join(
        shlex.quote(pattern) for pattern in GNOME_APP_RESETS[application_reset])
    # These are disposable eval-task processes. TERM invites unsaved-document handlers
    # (Pinta survived TERM after paint_line and contaminated task 6); KILL is the reset
    # contract: no save prompt, no graceful refusal, no task state survives.
    commands.append(
        f'for p in {patterns}; do pkill -KILL -i -f "$p" || true; done')
    # If none of the allowlisted terminal names matched, `exit` still closes the reset
    # shell. A real cleanup/settings failure occurs before the tolerant process loop and
    # reaches the visible marker. The camera verifier remains authoritative about stale
    # windows; process naming is never trusted as proof.
    commands.append("exit")
    return " && ".join(commands) + " || echo KVM_RESET_FAILED"


def reset_gnome_session(r4, cleanup_files=(), setting_resets=(),
                        application_reset="battery-apps", settle_s=3.0):
    """Type an allowlisted cleanup into a visible terminal through physical HID."""
    command = build_gnome_reset_command(
        cleanup_files, setting_resets, application_reset)
    r4.combo("ctrl+alt+t")
    time.sleep(1.0)
    r4.type(command)
    r4.key("enter")
    time.sleep(settle_s)
    return command


def reboot():
    """Full restart of the physical target between battery tasks. v1: the operator
    does it by hand; their Enter IS the readiness signal (desktop up and settled)."""
    input("[target] Power-cycle the laptop (full shutdown + boot). "
          "Press Enter when the desktop is up and settled... ")


def is_up():
    """v1 contract: True once reboot() returned (the operator confirmed). When a real
    backend lands this becomes an actual readiness probe."""
    return True


def verify_hid(r4, cam, screen=(1920, 1080), thresh=20.0, settle_s=4.0, attempts=2,
               shell=None):
    """Functional HID gate: prove the keyboard AND mouse collections actually deliver
    to the target OS, camera-verified. The firmware's probe flags can LIE -- 2026-07-21:
    a post-reboot half-dead composite device reported mouse_online=true while every
    click vanished between the laptop's USB host and Windows (the I2 class, physical
    edition; keyboard on the same device worked). The camera is the only truth.

    Round-trips are anchored per shell (the laptop switched from Windows 10 to
    Ubuntu/GNOME on 2026-07-21; shell defaults to CFG.target_shell):
      gnome:   keyboard = Super tap (opens Activities, Esc closes -- also correct on
               Windows, where Super opens Start); mouse = click the Activities
               corner (TOP-left).
      windows: keyboard = Win+R (Run dialog; Esc closes); mouse = click the Start
               button (BOTTOM-left).
    thresh=20.0 sits far above taskbar/widget churn (~5-12) and far below a real
    overview/dialog/menu appearing (measured 60-200+).

    Contamination-hardened (same day, second bug): a leftover OPEN Run dialog makes
    win+r a no-op (diff ~0) and an UNFOCUSED one ignores Esc -- reading falsely as
    "keyboard dead". Each round-trip therefore runs up to `attempts` times: pre-Esc to
    dismiss leftovers, and a small diff triggers a clean retry (the first attempt's
    post-Esc closes a refocused leftover, so the retry measures a real open). Returns
    (ok: bool, detail: str)."""
    # The tile-max metric comes from the package (its single home, 2026-07-21) --
    # no more package->script import of agent_loop_holo._frame_diff_score, which
    # also dragged the loop's import-time side effects (debug-dir makedirs) into
    # every verify_hid caller.
    from kvm_agent.config import CFG
    from kvm_agent.hardware.env import tile_max_diff_png, wait_until_stable
    # cam.seq is a property (an int), so wrap it -- and a wedged capture must not
    # read as "stable" (second review #1).
    seq_fn = (lambda: cam.seq) if hasattr(cam, "seq") else None
    shell = shell or CFG.target_shell
    if shell not in ("gnome", "windows"):
        raise ValueError(f"unknown shell {shell!r} (expected 'gnome' or 'windows')")

    def round_trip(fire):
        """esc -> settle -> before -> fire() -> settle -> diff -> esc -> settle.
        Returns the diff of the first attempt that beats thresh, else the last diff."""
        diff = 0.0
        for _ in range(attempts):
            r4.key("esc")
            wait_until_stable(cam.read, 1.0, seq_fn=seq_fn)
            before = cam.png_bytes()
            fire()
            wait_until_stable(cam.read, settle_s, seq_fn=seq_fn)
            diff = tile_max_diff_png(before, cam.png_bytes())
            r4.key("esc")
            wait_until_stable(cam.read, 1.0, seq_fn=seq_fn)
            if diff > thresh:
                break
        return diff

    if shell == "gnome":
        kbd_probe, kbd_name = lambda: r4.key("win"), "super"
        # Activities corner, TOP-left (fraction-based: ~2% in, ~1.5% down).
        mouse_at = (max(10, int(screen[0] * 0.02)), max(10, int(screen[1] * 0.015)))
    else:
        kbd_probe, kbd_name = lambda: r4.combo("win+r"), "win+r"
        mouse_at = (20, screen[1] - 25)   # Start button, bottom-left

    kbd_diff = round_trip(kbd_probe)
    if kbd_diff <= thresh:
        return False, f"keyboard NOT delivering ({kbd_name} diff {kbd_diff:.1f} <= {thresh})"

    def shell_click():
        r4.move(*mouse_at)
        r4.click()

    mouse_diff = round_trip(shell_click)
    if mouse_diff <= thresh:
        return False, f"mouse NOT delivering ({shell} corner click diff {mouse_diff:.1f} <= {thresh})"
    return True, f"hid ok ({shell}: kbd diff {kbd_diff:.1f}, mouse diff {mouse_diff:.1f})"
