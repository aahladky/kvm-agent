"""
pico_env.py — DesktopEnv-compatible environment backed by the physical KVM rig
(HDMI capture for observation, Pico HID for action).

This is a drop-in replacement for the OSWorld `DesktopEnv` so the OFFICIAL
`EvoCUAAgent` (mm_agents/evocua) runs UNMODIFIED — both S1 and S2 modes. The agent
emits pyautogui code strings already projected to real screen pixels; the repo's
env execs them in a VM via `controller.execute_python_command`. We exec the same
code against a Pico-backed `pyautogui` shim, and serve observations off the capture
card. WAIT/FAIL/DONE are handled exactly like DesktopEnv.step.

Interface implemented (the subset the agent + a thin run loop touch):
  env.reset(task_config) -> obs            env.controller.get_screenshot() -> png bytes
  env._get_obs() -> {"screenshot": ...}    env.controller.execute_python_command(code)
  env.step(action, pause) -> obs,r,done,info   env.controller.start/end_recording()
  env.evaluate()/.close()/.vm_ip/.action_space
"""
import sys
import time
import threading
import cv2
import numpy as np
from kvm_agent.config import CFG
from kvm_agent.hardware.pico_client import R4
from kvm_agent.hardware.appliance import ApplianceClient


def wait_until_stable(read_fn, max_s, stable_frames=3, thresh=2.0, poll_s=0.05):
    """Wait up to max_s for the screen to STOP changing, returning as soon as
    `stable_frames` consecutive polls show a mean-abs diff below `thresh` on a 160x90
    grayscale downscale (small enough to ignore capture-sensor noise, big enough to catch
    structural UI change). Replaces blind post-action sleeps: fast actions proceed
    immediately, slow-rendering apps still get the full window. 2026-07-18."""
    end = time.time() + max_s
    prev = None
    stable = 0
    while time.time() < end:
        f = read_fn()
        if f is not None:
            curr = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.int16)
            if prev is not None:
                if float(np.abs(curr - prev).mean()) < thresh:
                    stable += 1
                    if stable >= stable_frames:
                        return
                else:
                    stable = 0
            prev = curr
        time.sleep(poll_s)


def make_hid_client():
    """Pick the action channel: the Pi 5 + Pico UART appliance (default) or the retired
    WiFi Pico. Both expose the same R4 method surface, so the rest of PicoEnv is agnostic."""
    if CFG.hid_kind == "wifi":
        return R4()
    return ApplianceClient()

# Windows target: Media Foundation (MSMF), NOT DirectShow -- the Acer USB3 card delivers
# YUY2 there and cv2's DSHOW backend mis-reads its stride and ghosts stale frames into the
# current one (the "wallpaper duplicated at two scales" artifact, 2026-06-19); OBS and MSMF
# both decode it cleanly. Linux host (the VM-based rig, 2026-07): V4L2 is the native/only
# real backend for a UVC capture card -- CAP_MSMF doesn't exist outside Windows.
_CAPTURE_BACKEND = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_V4L2


class Camera:
    def __init__(self, index=0, w=1920, h=1080):
        self.index = index
        self.frame = None
        self._thread = None
        self._open(w, h)

    def _open(self, w, h):
        # MSMF is slow to OPEN (~20-25s one-time Media Foundation init) on Windows, hence
        # the longer first-frame wait below; once open, the threaded read drains fresh
        # frames. V4L2 on Linux opens fast by comparison.
        self.cap = cv2.VideoCapture(self.index, _CAPTURE_BACKEND)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.run = True   # set_resolution() sets this False to stop the OLD thread first
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        t0 = time.time()
        while self.frame is None:
            if time.time() - t0 > 15:
                raise SystemExit("no frames — is the capture card free (other process holding it)?")
            time.sleep(0.05)
        # discard the first few frames: MSMF's first frame post-open can be torn
        for _ in range(8):
            time.sleep(0.03)
        self.actual_h, self.actual_w = self.frame.shape[:2]

    def set_resolution(self, w, h):
        """Re-request the capture resolution on an ALREADY-OPEN device (2026-07-19).

        CORRECTION to an earlier, wrong assumption in this file: reading self.frame.shape
        after cap.set() does NOT reveal the true incoming HDMI signal on this hardware (a
        Macrosilicon MS21xx-class USB3 capture dongle). Tested directly: requesting 1920x1080
        returns a clean 1920x1080 frame, and separately requesting 1280x720 returns a clean
        1280x720 frame -- the chip has its own internal scaler and complies with WHATEVER
        cv2.set() asks for, regardless of the actual guest/host render resolution. So
        frame.shape after cap.set(w, h) is circular: it will always equal (h, w), never a
        genuine mismatch signal. There is no way to learn the true incoming resolution from
        the capture device itself.

        The only reliable source of truth is the GUEST's own reported resolution (e.g.
        `pyautogui.size()` run inside the VM via WAA's execute channel -- see
        waa/runner.py's query_guest_resolution()). Call this method with THAT value once it's
        known, so the capture request matches the guest's actual render size 1:1 instead of
        capturing at a stale/wrong resolution and round-tripping through the chip's scaler
        (request 1920x1080 against a genuinely-720p guest = the chip upscales 720p content to
        1080p, we then downscale it back to 720p for the model in png_bytes() -- a lossy
        no-op round trip that was happening silently before this existed).

        Does a full stop-thread -> release -> reopen -> restart-thread cycle, NOT a live
        cap.set() on the already-streaming device (tried that first: it errored with
        "VIDIOC_REQBUFS: Device or resource busy" and crashed the background reader thread
        with an assertion inside cv2's V4L2 backend -- V4L2 will not renegotiate the buffer
        format while capture is in flight; the device has to be reopened, exactly like
        __init__ does it safely before any threading starts).
        """
        self.run = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.cap.release()
        self._open(w, h)
        return self.actual_w, self.actual_h

    def _loop(self):
        while self.run:
            ok, f = self.cap.read()
            if ok:
                self.frame = f

    def read(self):
        return self.frame

    def png_bytes(self, full_res=False):
        # Downscale 1080p -> 720p before encoding: vision-token count scales with pixels
        # (measured 2026-07-17: 1/4 the pixels ~ -35% prompt tokens, and format does NOT
        # matter, only resolution) -- this is the single biggest per-step latency lever.
        # Safe for grounding because Holo outputs [0,1000] normalized coords and
        # agent_loop_holo projects them against the REAL screen size, not this PNG.
        # full_res=True skips the downscale for EVIDENCE frames (grading/verify/reference):
        # tesseract OCR on a 720p analog-capture frame produces garbage (proven 2026-07-18,
        # calc_basic's "56" unreadable) -- the model reads 720p, the graders read 1080p.
        frame = self.frame if full_res else cv2.resize(self.frame, (1280, 720))
        ok, buf = cv2.imencode(".png", frame)
        return buf.tobytes()

    def release(self):
        self.run = False
        # Join the capture thread BEFORE releasing the device. Releasing cap while _loop is
        # blocked inside cap.read() frees the device under an in-flight read -> native abort
        # (the "exception not rethrown / Aborted (core dumped)" SIGABRT seen 2026-07-17, flaw
        # #5). Joining bounds the wait; the common case (read returns within a frame interval)
        # closes the race cleanly. If a read is genuinely wedged past the timeout we still
        # release (best effort) rather than hang shutdown forever.
        t = getattr(self, "_thread", None)
        if t is not None:
            t.join(timeout=2.0)
        self.cap.release()


class PicoPyAutoGUI:
    """Minimal `pyautogui` surface mapped to Pico HID. All coordinates are real
    screen pixels (the agent already projected them to the screenshot resolution,
    which equals our 1920x1080 capture)."""

    def __init__(self, r4):
        self.r4 = r4

    # --- mouse ---
    def click(self, x=None, y=None, clicks=1, interval=0.0, button="left", duration=0.0, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        for _ in range(int(clicks)):
            self.r4.rclick() if button == "right" else self.r4.click()
            if interval:
                time.sleep(interval)

    def rightClick(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.rclick()

    def middleClick(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.click()  # Pico has no middle button; approximate

    def doubleClick(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.click(); self.r4.click()

    def tripleClick(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.click(); self.r4.click(); self.r4.click()

    def moveTo(self, x, y, duration=0.0, **kw):
        self.r4.move(int(x), int(y))

    def dragTo(self, x, y, duration=0.0, button="left", **kw):
        self.r4.down(); self.r4.move(int(x), int(y)); self.r4.up()

    def mouseDown(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.down()

    def mouseUp(self, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.up()

    def scroll(self, clicks, x=None, y=None, **kw):
        if x is not None and y is not None:
            self.r4.move(int(x), int(y))
        self.r4.scroll(int(clicks))

    # --- keyboard ---
    def press(self, keys, presses=1, interval=0.0, **kw):
        keys = keys if isinstance(keys, (list, tuple)) else [keys]
        for _ in range(int(presses)):
            for k in keys:
                self.r4.key(str(k))
                if interval:
                    time.sleep(interval)

    def hotkey(self, *keys, **kw):
        self.r4.combo("+".join(str(k) for k in keys))

    def keyDown(self, key, **kw):
        # Pico client has no held-key primitive; held modifiers (e.g. shift-hold)
        # aren't supported. No-op to avoid spurious taps. (Not needed for the
        # calculator probe; revisit if a task needs stateful holds.)
        pass

    def keyUp(self, key, **kw):
        pass

    def typewrite(self, message, interval=0.0, **kw):
        self.r4.type(str(message))

    write = typewrite

    def position(self):
        return (0, 0)


class PicoController:
    """Stands in for OSWorld's PythonController. Backs get_screenshot off the
    capture card and execute_python_command off the Pico pyautogui shim."""

    def __init__(self, cam, r4):
        self.cam = cam
        self.pg = PicoPyAutoGUI(r4)
        self._exec_globals = {"pyautogui": self.pg, "time": time}

    def get_screenshot(self):
        return self.cam.png_bytes(full_res=CFG.holo_model_input_full_res)

    def execute_python_command(self, command):
        exec(command, self._exec_globals)

    def execute_action(self, action):
        # WAIT/FAIL/DONE are handled in PicoEnv.step; nothing to do here.
        pass

    def start_recording(self):
        pass

    def end_recording(self, path=None):
        pass

    def get_accessibility_tree(self):
        return None

    def get_terminal_output(self):
        return None

    def get_vm_platform(self):
        return "physical"

    def get_vm_screen_size(self):
        return {"width": self.cam_w, "height": self.cam_h}


class PicoEnv:
    """DesktopEnv-shaped env over the physical rig."""

    def __init__(self, cam_index=0, screen_size=(1920, 1080),
                 reset_coord=(534, 630), reset_settle=1.5, show=False):
        # screen_size is a best-effort FALLBACK REQUEST, not a source of truth of any kind.
        # CORRECTED 2026-07-19: an earlier version of this comment claimed Camera "reads
        # back what it actually negotiated" and treated that as ground truth -- WRONG. The
        # physical capture chip has its own internal scaler and complies with whatever
        # resolution cv2.set() asks for regardless of the true guest/host render size (see
        # Camera.set_resolution's docstring), so self.cam.actual_w/h only ever echoes the
        # request back -- it cannot detect a genuine mismatch. The ONLY reliable source of
        # truth is the guest's own self-reported resolution (pyautogui.size() run inside the
        # VM). This constructor has no way to query that (no guest exec channel at this
        # layer, by design -- see the module docstring's "nothing installed on the target"
        # premise; the exec channel only exists because WAA's benchmark server is installed
        # for evaluation purposes). Callers with guest access (waa/runner.py) MUST call
        # sync_to_guest_resolution() below once they know the true value; screen_size here
        # is only what gets used until/unless that happens.
        self.cam = Camera(cam_index, *screen_size)
        self.screen_width, self.screen_height = self.cam.actual_w, self.cam.actual_h
        try:
            self.r4 = make_hid_client()   # appliance (default) or legacy WiFi, per CFG.hid_kind
        except Exception:
            try:
                self.cam.release()   # don't orphan the capture device if the HID client fails
            except Exception:
                pass
            raise
        self._sync_appliance_screen()
        self.controller = PicoController(self.cam, self.r4)
        self.controller.cam_w, self.controller.cam_h = self.screen_width, self.screen_height
        self.vm_ip = None
        self.action_space = "pyautogui"
        self.instruction = None
        self.reset_coord = reset_coord
        self.reset_settle = reset_settle
        self.action_history = []
        self.show = show
        f = self.cam.read()
        print(f"[pico_env] capture {f.shape[1]}x{f.shape[0]} (fallback request, NOT verified "
              f"against the guest -- call sync_to_guest_resolution() once known)")

    def _sync_appliance_screen(self):
        """Tell the HID appliance the resolution we're currently using, so its pixel->wire-
        range scale factor (SCREEN_W/H in appliance/pi5/hid_bridge.py) matches. The legacy
        WiFi R4 client has no equivalent (the Pico firmware does this scaling itself, via its
        own hardcoded SCREEN_W/H) -- no-op there, guarded rather than assumed."""
        set_screen = getattr(self.r4, "set_screen", None)
        if set_screen is None:
            return
        try:
            set_screen(self.screen_width, self.screen_height)
        except Exception as e:
            print(f"[pico_env] WARNING: could not sync HID appliance to {self.screen_width}x"
                  f"{self.screen_height}: {e} -- clicks may be miscalibrated")

    def sync_to_guest_resolution(self, w, h):
        """Re-point the ENTIRE pipeline at the guest's TRUE, freshly-queried resolution
        (2026-07-19) -- the fix for a real bug: two live validation runs today captured at a
        stale 1920x1080 request while the guest was actually rendering at 1280x720 the whole
        time (confirmed via pyautogui.size() run inside the VM), so every step silently paid
        for an upscale-to-1080p-then-downscale-back-to-720p round trip for nothing. Call this
        after the guest is in its final state for the session (post VM-revert, WAA server up)
        and BEFORE running any task, so capture/HID math match what's actually on screen from
        the very first frame -- not just eventually via Camera's own no-op negotiation echo.
        Re-requests the capture resolution (Camera.set_resolution), updates
        screen_width/height + controller.cam_w/h from what was ACTUALLY delivered after the
        re-request, and re-syncs the HID appliance."""
        actual_w, actual_h = self.cam.set_resolution(w, h)
        if (actual_w, actual_h) != (w, h):
            print(f"[pico_env] WARNING: requested capture at guest resolution {w}x{h} but "
                  f"got {actual_w}x{actual_h} back -- using the actual delivered size")
        self.screen_width, self.screen_height = actual_w, actual_h
        self.controller.cam_w, self.controller.cam_h = actual_w, actual_h
        self._sync_appliance_screen()
        print(f"[pico_env] synced to guest's true resolution: {self.screen_width}x{self.screen_height}")

    def _settle(self, secs):
        # Smart settle (2026-07-18): return as soon as the UI stops changing instead of
        # always burning the full blind wait.
        if not self.show:
            wait_until_stable(self.cam.read, secs)
            return
        end = time.time() + secs
        while time.time() < end:
            f = self.cam.read()
            if f is not None:
                cv2.imshow("capture", f); cv2.waitKey(15)
            else:
                time.sleep(0.01)

    def reset(self, task_config=None, **kw):
        """env.reset analog — AC-click to clear the display to 0."""
        self.action_history = []
        self.instruction = (task_config or {}).get("instruction")
        x, y = self.reset_coord
        self.r4.move(x, y); self.r4.click()
        time.sleep(self.reset_settle)
        return self._get_obs()

    def _get_obs(self):
        return {
            "screenshot": self.controller.get_screenshot(),
            "accessibility_tree": None,
            "terminal": None,
            "instruction": self.instruction,
        }

    def observe(self):
        """Current screen as an obs dict, WITHOUT any physical action. reset() AC-clicks
        a calculator coordinate (benchmark-specific); the interactive operator must NOT
        click anything just to look at the screen, so it calls this instead."""
        return self._get_obs()

    def step(self, action, pause=5.0):
        self.action_history.append(action)
        reward, done, info = 0, False, {}
        self.last_exec_s = 0.0   # HID execution time for this action (read by timing logs)
        if action in ("WAIT", "FAIL", "DONE", "ANSWER"):
            if action == "WAIT":
                time.sleep(pause)
            elif action == "FAIL":
                done, info = True, {"fail": True}
            elif action == "DONE":
                done, info = True, {"done": True}
            elif action == "ANSWER":
                # PATCH(answer-channel): communicative step. The text rides on the agent
                # (agent.last_answer); the run loop surfaces it. Nothing to execute and no
                # screen change to wait for, so skip the settle and return immediately.
                info = {"answer": True}
                return self._get_obs(), reward, done, info
        else:
            _t = time.time()
            try:
                self.controller.execute_python_command(action)
            except Exception as e:
                print(f"  [exec error] {e}  ::  {action!r}")
            self.last_exec_s = time.time() - _t
        self._settle(pause)
        return self._get_obs(), reward, done, info

    def end_full_png(self):
        return self.cam.png_bytes()

    def evaluate(self):
        # No programmatic OSWorld evaluator on real hardware. End-state is saved
        # by the runner and OCR-scored in the sandbox (score_batch.py).
        return None

    def close(self):
        try:
            self.r4.up()  # safety: release any held mouse button before disconnecting
        except Exception:
            pass
        try:
            self.cam.release()
        except Exception:
            pass
        try:
            self.r4.close()
        except Exception:
            pass
        cv2.destroyAllWindows()
