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
import time
import threading
import cv2
from kvm_agent.hardware.pico_client import R4


class Camera:
    def __init__(self, index=0, w=1920, h=1080):
        # Use Media Foundation (MSMF), NOT DirectShow. On the Windows target the Acer
        # USB3 card delivers YUY2, and cv2's DSHOW backend mis-reads its stride and
        # ghosts stale frames into the current one (the "wallpaper duplicated at two
        # scales" artifact, 2026-06-19) — OBS and MSMF both decode it cleanly. MSMF is
        # slow to OPEN (~20-25s one-time Media Foundation init), hence the longer
        # first-frame wait below; once open, the threaded read drains fresh frames.
        self.cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.run = True
        threading.Thread(target=self._loop, daemon=True).start()
        t0 = time.time()
        while self.frame is None:
            if time.time() - t0 > 15:
                raise SystemExit("no frames — is the capture card free (other process holding it)?")
            time.sleep(0.05)
        # discard the first few frames: MSMF's first frame post-open can be torn
        for _ in range(8):
            time.sleep(0.03)

    def _loop(self):
        while self.run:
            ok, f = self.cap.read()
            if ok:
                self.frame = f

    def read(self):
        return self.frame

    def png_bytes(self):
        ok, buf = cv2.imencode(".png", self.frame)
        return buf.tobytes()

    def release(self):
        self.run = False
        time.sleep(0.1)
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
        return self.cam.png_bytes()

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
        self.screen_width, self.screen_height = screen_size
        self.cam = Camera(cam_index, *screen_size)
        try:
            self.r4 = R4()
        except Exception:
            try:
                self.cam.release()   # don't orphan the capture device if the Pico is offline
            except Exception:
                pass
            raise
        self.controller = PicoController(self.cam, self.r4)
        self.controller.cam_w, self.controller.cam_h = screen_size
        self.vm_ip = None
        self.action_space = "pyautogui"
        self.instruction = None
        self.reset_coord = reset_coord
        self.reset_settle = reset_settle
        self.action_history = []
        self.show = show
        f = self.cam.read()
        print(f"[pico_env] capture {f.shape[1]}x{f.shape[0]}  (must equal Pico SCREEN_W/H)")

    def _settle(self, secs):
        end = time.time() + secs
        while time.time() < end:
            f = self.cam.read()
            if self.show and f is not None:
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
