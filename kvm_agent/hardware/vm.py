"""VM reset via libvirt snapshot revert + a forced cold reboot -- the fix for harness
flaw #7 (no reset between battery tasks, which let each task start atop the leftover
windows of every prior task and invalidated the multi-task runs).

Strategy (revised 2026-07-18 -- see the "warm revert breaks HID" finding below): an
internal snapshot (memory + disk) of the target VM sitting at a clean, logged-in desktop
gives disk-level correctness -- revert_clean() reverts to it, THEN forces a full cold
reboot (shutdown + start) before the VM is handed back for the next task. The revert
alone would be faster (~15s, no Windows boot), but is NOT reliable with this rig's
passed-through USB HID device (below); the reboot costs another ~35-45s but has been
100% reliable every time it was tested live.

Prerequisites proven on this rig (see docs/FINDINGS_2026-07-18_harness_review.md #7):
  - The VM's UEFI NVRAM had to be converted raw -> qcow2, else libvirt refuses internal
    snapshots ("internal snapshots of a VM with pflash based firmware require QCOW2 nvram
    format"). Done once; the domain XML now points at *_VARS_nosb.qcow2.
  - **Warm snapshot revert reliably breaks the passed-through USB HID device, even from
    a freshly-taken, correctly-configured (single hostdev) snapshot.** Measured live
    2026-07-18: after a `snapshot-revert --running`, the desktop rendered PERFECTLY (the
    pixel-based verify passed) but every click/keypress ACKed at the Pico and never
    reached the guest OS -- confirmed with a real round-trip check (_verify_hid: toggle
    NumLock via HID, read the LED back). A cold reboot (shutdown+start) restored it every
    single time this was tested (3/3). Root cause: QEMU's snapshot only rewinds the
    GUEST's memory-resident belief about the USB device's state; a physical passthrough
    device's actual internal state (toggle bits, endpoint state) lives on the real
    hardware and can't be rewound by a memory snapshot, so revert leaves a real/guest
    state mismatch -- this is a known general limitation of USB passthrough + snapshots/
    migration, not specific to the Pico. A SEPARATE bug (a stale duplicate <hostdev> entry
    for the old CircuitPython-era VID:PID left in the domain's persistent config after the
    PiKVM firmware port's VID:PID swap) was found and fixed the same day and made the
    symptom worse, but did not fully explain it -- a clean single-hostdev revert still
    broke HID. Always keep the cold reboot; do not "optimize" it back to a bare revert
    without re-proving HID survives across multiple real revert cycles.
  - The VM's display only reaches the capture card via `virt-viewer --full-screen` (SPICE
    fullscreened onto the physical monitor the capture card is wired to -- see
    FINDINGS_integration.md's "software display bridge" topology). That SPICE session does
    NOT survive either a snapshot revert or a reboot -- virt-viewer exits when the guest's
    state changes underneath it -- so revert_clean() relaunches it if it's not running, or
    the camera silently ends up looking at the bare host desktop instead of the VM
    (measured: a ~220/255 tile-max diff, caught by the reference-frame check below).

Shells out to `virsh` (qemu:///system) rather than taking a libvirt-python dependency --
matches how the rest of this project already drives the VM (see FINDINGS_integration.md).

Reality check, per the project's core rule (no success signal decoupled from the screen):
revert_clean() VERIFIES it landed in the expected state two independent ways -- diffing a
fresh capture against a reference frame saved when the snapshot was created (catches a
silent no-revert or an app auto-launched into the "clean" desktop), AND a keyboard HID
round-trip (toggle NumLock, read the LED back -- catches exactly the dead-HID-post-revert
failure above, which the pixel check alone cannot see since a broken input path doesn't
have to change the screen by itself).
"""
import os
import subprocess

from kvm_agent.config import CFG


class VMError(RuntimeError):
    pass


class VMController:
    def __init__(self, domain=None, snapshot=None, settle_s=None, ref_frame_path=None,
                 boot_wait_s=None):
        self.domain = domain or CFG.vm_domain
        self.snapshot = snapshot or CFG.vm_snapshot
        self.settle_s = CFG.vm_revert_settle if settle_s is None else settle_s
        self.boot_wait_s = CFG.vm_boot_wait if boot_wait_s is None else boot_wait_s
        # reference frame saved at snapshot-creation time; used to verify a revert really
        # restored the clean desktop. Defaults next to the snapshot name under runs_dir.
        self.ref_frame_path = ref_frame_path or os.path.join(
            CFG.runs_dir, f"vm_ref_{self.domain}_{self.snapshot}.png")

    # --- low-level virsh -------------------------------------------------------------
    def _virsh(self, *args, check=True, timeout=120):
        cmd = ["virsh", "-c", "qemu:///system", *args]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise VMError(f"virsh {' '.join(args)} failed ({p.returncode}): "
                          f"{p.stderr.strip() or p.stdout.strip()}")
        return p

    def state(self):
        return self._virsh("domstate", self.domain).stdout.strip()

    def has_snapshot(self, name=None):
        name = name or self.snapshot
        out = self._virsh("snapshot-list", self.domain, "--name", check=False).stdout
        return name in out.split()

    # --- baseline creation -----------------------------------------------------------
    def create_clean_snapshot(self, capture_fn=None, overwrite=False):
        """Snapshot the CURRENT running state as the clean-desktop baseline. Call this once,
        with the VM booted to a pristine logged-in desktop (no app windows open) and the
        Pico HID confirmed alive. If `capture_fn` is given, also save a reference frame so
        later reverts can be verified against reality."""
        if self.state() != "running":
            raise VMError(f"{self.domain} must be RUNNING at a clean desktop to snapshot "
                          f"(state={self.state()!r})")
        if self.has_snapshot():
            if not overwrite:
                raise VMError(f"snapshot {self.snapshot!r} already exists; pass overwrite=True "
                              "to replace it")
            self._virsh("snapshot-delete", self.domain, self.snapshot)
        self._virsh("snapshot-create-as", self.domain, self.snapshot,
                    "--description", "clean logged-in desktop baseline (battery reset)")
        if capture_fn is not None:
            os.makedirs(os.path.dirname(self.ref_frame_path), exist_ok=True)
            with open(self.ref_frame_path, "wb") as f:
                f.write(capture_fn())
            print(f"[vm] saved reference frame -> {self.ref_frame_path}")
        print(f"[vm] created clean snapshot {self.snapshot!r} on {self.domain}")

    # --- the reset -------------------------------------------------------------------
    def revert_clean(self, capture_fn=None, verify_threshold=6.0, check_hid=True,
                      cold_boot=True):
        """Revert the VM to the clean-desktop snapshot (the flaw #7 reset) for disk-level
        correctness, THEN force a full cold reboot (shutdown + start) before handing the VM
        back. `cold_boot=False` skips the reboot (fast, ~15s) but is NOT recommended for
        live battery use -- see the module docstring: a warm revert alone reliably leaves
        the passed-through USB HID device dead while the screen renders perfectly, which
        `check_hid` below exists specifically to catch.

        If `capture_fn` and a reference frame are available, verify the post-reset screen
        matches the baseline -- retrying virt-viewer relaunches a bounded number of times
        and RAISING if it still doesn't, since a task run against an unverified display
        (e.g. the capture card looking at the bare host desktop) produces garbage results
        that masquerade as model failures. `check_hid` (default
        True) additionally does a keyboard round-trip (toggle NumLock, read the LED back)
        against the appliance -- a real, camera-INDEPENDENT proof the passed-through USB HID
        device still functions; the pixel check alone cannot see a dead input path, since
        broken input doesn't have to change the screen by itself."""
        if not self.has_snapshot():
            raise VMError(f"no snapshot {self.snapshot!r} on {self.domain}; create it first "
                          "(python -m kvm_agent.hardware.vm --create)")
        self._virsh("snapshot-revert", self.domain, self.snapshot, "--running", timeout=180)
        if cold_boot:
            self._cold_reboot()
        else:
            self._settle()
        self._ensure_virt_viewer()
        if capture_fn is not None and os.path.exists(self.ref_frame_path):
            # A live virt-viewer process is not proof it's actually fullscreen -- GTK/SPICE
            # occasionally lands it windowed (observed repeatedly 2026-07-18, always fixed by
            # a fresh kill+relaunch). Retry the kill+relaunch a bounded number of times, and
            # if the screen STILL doesn't match the baseline, RAISE -- warn-and-continue here
            # meant a battery task once ran against a capture of the bare HOST desktop
            # (diff 191.8) and its result looked like a model failure. A task result produced
            # on an unverified display is worse than no result.
            for attempt in range(3):
                if self._verify(capture_fn, verify_threshold, warn=False):
                    break
                print(f"[vm] post-revert verify failed (attempt {attempt + 1}/3); "
                      "virt-viewer may not be fullscreen -- killing + relaunching")
                self._kill_virt_viewer()
                self._ensure_virt_viewer(force=True)
            else:
                raise VMError(
                    "post-revert screen does not match the clean-desktop baseline after 3 "
                    "virt-viewer relaunches -- refusing to run tasks against an unverified "
                    "display (capture is probably showing the host desktop, not the VM)")
        if check_hid:
            # A dead HID post-reset used to warn-and-continue, which then read as "the
            # model can't click" (2026-07-18: a WAA task burned 4 dead clicks and got
            # no-progress-aborted while the mouse collection was silently offline). One
            # extra cold reboot is the documented cure; raise if even that doesn't fix it.
            for attempt in range(2):
                if self._verify_hid():
                    break
                if attempt == 0:
                    print("[vm] HID dead after reset -- forcing one more cold reboot")
                    self._cold_reboot()
                    self._ensure_virt_viewer(force=True)
            else:
                raise VMError(
                    "USB HID (keyboard or mouse collection) still dead after an extra "
                    "cold reboot -- refusing to run tasks with a dead input path")
        return True

    def _verify_hid(self):
        """Toggle NumLock via the appliance and confirm the LED readback actually flipped --
        a real round trip through bridge->UART->Pico->USB HID->guest OS->keyboard driver,
        independent of the camera/display pipeline (see revert_clean's docstring for why the
        pixel check alone missed a real dead-HID-post-revert failure). ALSO require the
        firmware to report BOTH HID collections online: the composite device can come up
        keyboard-alive/mouse-dead (observed live 2026-07-18), which a keyboard round-trip
        alone cannot see. Returns True iff both checks pass."""
        try:
            from kvm_agent.hardware.appliance import ApplianceClient
            c = ApplianceClient()
            before = c.probe()["ack"]
            c.key("numlock")
            import time
            time.sleep(0.3)
            after = c.probe()["ack"]
            c.key("numlock")  # restore original state
            num_before = "num=1" in before
            num_after = "num=1" in after
            kbd_ok = "kbd=1" in after
            mouse_ok = "mouse=1" in after
            if num_before == num_after or not kbd_ok or not mouse_ok:
                print(f"[vm] HID check FAILED (numlock_flip={num_before != num_after}, "
                      f"kbd_online={kbd_ok}, mouse_online={mouse_ok}; probe={after!r})")
                return False
            print("[vm] HID round-trip verified (NumLock toggled + read back correctly, "
                  "both HID collections online)")
            return True
        except Exception as e:
            print(f"[vm] HID round-trip check skipped (appliance unreachable: {e})")
            return True  # no appliance -> nothing to check; don't block non-HID setups

    def _kill_virt_viewer(self):
        subprocess.run(["pkill", "-f", f"virt-viewer.*{self.domain}"], capture_output=True)
        import time
        time.sleep(1)

    def _settle(self):
        """Give the warm desktop + the passed-through USB-HID a moment to re-sync after the
        RAM state swap before the next task starts driving it."""
        if self.settle_s > 0:
            import time
            time.sleep(self.settle_s)

    def _cold_reboot(self):
        """Force a full shutdown+start cycle right after the snapshot revert -- the fix for
        warm revert reliably breaking the passed-through USB HID device (see module
        docstring). ACPI shutdown first (clean, fast when the guest is idle at a desktop,
        which it always is right after a revert); falls back to `destroy` if the guest
        doesn't respond within a bounded timeout (mirrors the mid-Windows-Update hang seen
        earlier this project -- shouldn't happen here since we just reverted to an idle
        desktop, but don't hang the whole battery run on it if it does)."""
        import time
        self._virsh("shutdown", self.domain, check=False)
        for _ in range(15):  # ~15s bounded wait for a clean ACPI shutdown
            if self.state() == "shut off":
                break
            time.sleep(1)
        else:
            print("[vm] ACPI shutdown did not complete in time -- forcing off")
            self._virsh("destroy", self.domain, check=False)
            for _ in range(10):
                if self.state() == "shut off":
                    break
                time.sleep(1)
        self._virsh("start", self.domain, timeout=60)
        print(f"[vm] cold-rebooted; waiting {self.boot_wait_s:.0f}s for the desktop")
        time.sleep(self.boot_wait_s)

    def _ensure_virt_viewer(self, force=False):
        """The SPICE session virt-viewer holds does not survive a snapshot revert (the
        client exits when the guest's RAM state gets swapped underneath it) -- without this,
        the capture card silently ends up looking at the bare host desktop instead of the VM.
        Relaunch it (fullscreened onto the monitor-mapping configured in
        ~/.config/virt-viewer/settings) if it's not already running for this domain, or
        unconditionally when `force` (the self-heal retry in revert_clean)."""
        if not force:
            check = subprocess.run(["pgrep", "-f", f"virt-viewer.*{self.domain}"], capture_output=True)
            if check.returncode == 0:
                return  # already running
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        try:
            subprocess.Popen(
                ["virt-viewer", "--connect", "qemu:///system", "--full-screen",
                 "--domain-name", self.domain],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            print(f"[vm] virt-viewer was down after revert -- relaunched for {self.domain}")
            import time
            time.sleep(4)  # let the SPICE session establish + go fullscreen before any capture
        except FileNotFoundError:
            print("[vm] WARNING: virt-viewer not found -- capture will see the host desktop, "
                  "not the VM, until it's relaunched manually")

    def _verify(self, capture_fn, threshold, warn=True):
        from agent_loop_holo import _frame_diff_score
        with open(self.ref_frame_path, "rb") as f:
            ref = f.read()
        # drop_bottom_row: the taskbar strip (clock / weather-widget text / badges) churns
        # on its own between the reference frame and any later verify -- it aborted a whole
        # battery run 2026-07-18 over a weather-text change on an otherwise clean desktop.
        score = _frame_diff_score(ref, capture_fn(), drop_bottom_row=True)
        if score > threshold:
            if warn:
                print(f"\n!!! WARNING: post-revert screen differs from the clean-desktop reference "
                      f"(tile-max diff {score:.1f} > {threshold}). The revert may not have landed "
                      f"in the expected state (an app auto-launched? wrong snapshot? capture drift? "
                      f"virt-viewer not fullscreen?). Task is running on an UNVERIFIED reset.\n")
            return False
        else:
            print(f"[vm] revert verified clean (diff {score:.1f} <= {threshold})")
            return True


def make_vm_controller():
    """Live VMController from CFG, or None when VM reset is disabled (CFG.vm_reset=0)."""
    return VMController() if CFG.vm_reset else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VM snapshot reset control (flaw #7).")
    ap.add_argument("--create", action="store_true",
                    help="snapshot the CURRENT running state as the clean-desktop baseline")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing baseline")
    ap.add_argument("--revert", action="store_true", help="revert to the clean-desktop baseline")
    ap.add_argument("--status", action="store_true", help="show domain state + snapshot presence")
    ap.add_argument("--no-ref", action="store_true",
                    help="skip the reference-frame capture/verify (no camera available)")
    args = ap.parse_args()

    vm = VMController()
    cap_fn = None
    loop_mod = None
    if not args.no_ref:
        try:
            import agent_loop_holo as loop_mod
            loop_mod.boot()
            cap_fn = loop_mod._frame_png_full   # full-res: reference/verify are evidence frames
        except Exception as e:
            print(f"[vm] camera unavailable ({e}); proceeding without reference frame")
            loop_mod = None

    try:
        if args.status or not (args.create or args.revert):
            print(f"domain={vm.domain} state={vm.state()} "
                  f"snapshot({vm.snapshot})={'present' if vm.has_snapshot() else 'MISSING'} "
                  f"ref_frame={'present' if os.path.exists(vm.ref_frame_path) else 'missing'}")
        if args.create:
            vm.create_clean_snapshot(capture_fn=cap_fn, overwrite=args.overwrite)
        if args.revert:
            vm.revert_clean(capture_fn=cap_fn)
            print("reverted.")
    finally:
        # flaw #5: Camera.release() joins its capture thread before cap.release() --
        # but only if actually called. Skipping this left the camera thread torn down
        # by bare process exit instead, which is exactly the race that fix guards against.
        if loop_mod is not None:
            loop_mod.shutdown()
