"""
env.py — physical-rig environment for the Holo agent loop: HDMI capture for
observation (Camera) + the Pi 5 / Pico UART HID appliance for action (r4).

History: this file was `pico_env.py`, a DesktopEnv-compatible shim so the OFFICIAL
EvoCUAAgent could run unmodified (pyautogui exec-shim, OSWorld step/reset/evaluate).
EvoCUA was archived 2026-07-20 (AGENTS.md §3 — the shim's last full version is at
_archive/old-stack/kvm_agent/hardware/env.py). The live consumer is agent_loop_holo.py,
which talks to env.cam and env.r4 directly and never used the shim.

Live surface: wait_until_stable, make_hid_client, Camera, PicoEnv
(observe/_settle/close, plus the .cam and .r4 attributes).
"""
import sys
import time
import threading
import cv2
import numpy as np
from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceClient


def wait_until_stable(read_fn, max_s, stable_frames=3, thresh=2.0, poll_s=0.05):
    """Wait up to max_s for the screen to STOP changing, returning as soon as
    `stable_frames` consecutive polls show a mean-abs diff below `thresh` on a 160x90
    grayscale downscale. Replaces blind post-action sleeps: fast actions proceed
    immediately, slow-rendering apps still get the full window. 2026-07-18.

    KNOWN-DEBT (fixed in the physical-target move): this is the whole-frame-mean
    metric flaw #4 discredited for change DETECTION; it survives here only because
    settle waits for "stop changing", not "did it change". Tile-max port is planned.
    """
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
    """The action channel: the Pi 5 + Pico UART appliance (the retired WiFi Pico path
    was archived 2026-07-20; see _archive/old-stack/kvm_agent/hardware/pico_client.py)."""
    return ApplianceClient()

# Windows target: Media Foundation (MSMF), NOT DirectShow -- the Acer USB3 card delivers
# YUY2 there and cv2's DSHOW backend mis-reads its stride and ghosts stale frames into the
# current one (the "wallpaper duplicated at two scales" artifact, 2026-06-19); OBS and MSMF
# both decode it cleanly. Linux host: V4L2 is the native/only real backend for a UVC
# capture card -- CAP_MSMF doesn't exist outside Windows.
_CAPTURE_BACKEND = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_V4L2


# NOTE: Camera is carried over VERBATIM from the previous env.py (lines 65-120 of the
# pre-rewrite file, now at _archive/old-stack/kvm_agent/hardware/env.py). It is replaced
# wholesale by the FrameBuffer version in the frame-freshness task of this plan.


class Camera:
    def __init__(self, index=0, w=1920, h=1080):
        # MSMF is slow to OPEN (~20-25s one-time Media Foundation init) on Windows, hence
        # the longer first-frame wait below; once open, the threaded read drains fresh
        # frames. V4L2 on Linux opens fast by comparison.
        self.cap = cv2.VideoCapture(index, _CAPTURE_BACKEND)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.frame = None
        self.run = True
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


class PicoEnv:
    """Capture + HID bundle for the physical rig. The live loop touches .cam and .r4
    directly; observe() is the no-side-effect observation path."""

    def __init__(self, cam_index=0, screen_size=(1920, 1080), show=False):
        self.screen_width, self.screen_height = screen_size
        self.cam = Camera(cam_index, *screen_size)
        try:
            self.r4 = make_hid_client()
        except Exception:
            try:
                self.cam.release()   # don't orphan the capture device if the HID client fails
            except Exception:
                pass
            raise
        self.show = show
        f = self.cam.read()
        print(f"[env] capture {f.shape[1]}x{f.shape[0]}")

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

    def observe(self):
        """Current screen as model-input PNG bytes (720p downscale unless
        CFG.holo_model_input_full_res), WITHOUT any physical action."""
        return {"screenshot": self.cam.png_bytes(full_res=CFG.holo_model_input_full_res)}

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
