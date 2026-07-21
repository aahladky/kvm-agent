"""
env.py — physical-rig environment for the Holo agent loop: HDMI capture for
observation (Camera) + the Pi 5 / Pico UART HID appliance for action (r4).

History: this file was `pico_env.py`, a DesktopEnv-compatible shim so the OFFICIAL
EvoCUAAgent could run unmodified (pyautogui exec-shim, OSWorld step/reset/evaluate).
EvoCUA was archived 2026-07-20 (AGENTS.md §3 — the shim's last full version is at
_archive/old-stack/kvm_agent/hardware/env.py). The live consumer is agent_loop_holo.py,
which talks to env.cam and env.r4 directly and never used the shim.

Live surface: wait_until_stable, make_hid_client, FrameBuffer, Camera, PicoEnv
(observe/_settle/close, plus the .cam and .r4 attributes).
"""
import sys
import time
import threading
import cv2
import numpy as np
from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceClient


def _tile_max_diff(prev, curr):
    """Max per-tile mean-abs diff over a 16x9 grid on a 480x270 downscale — the same
    tiling as agent_loop_holo._frame_diff_score (flaw #4 fix), for raw BGR frames.
    A small localized change (a typed char, a calc digit) registers strongly in its
    own tile instead of being averaged into nothing by the whole frame."""
    a = cv2.cvtColor(cv2.resize(prev, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.int16)
    b = cv2.cvtColor(cv2.resize(curr, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.int16)
    d = np.abs(a - b)
    return float(d.reshape(9, 30, 16, 30).mean(axis=(1, 3)).max())


def wait_until_stable(read_fn, max_s, stable_frames=3, thresh=3.0, poll_s=0.05):
    """Wait up to max_s for the screen to STOP changing, returning as soon as
    `stable_frames` consecutive polls show a tile-max diff below `thresh`. Replaces
    blind post-action sleeps: fast actions proceed immediately, slow-rendering apps
    still get the full window.

    Metric: tile-max (2026-07-20) — the old 160x90 whole-frame mean was the metric
    flaw #4 discredited for change detection; on analog capture its noise floor and
    the small-change signal overlap. thresh=3.0 matches FRAME_CHANGE_THRESHOLD's live
    calibration (2026-07-18: static=0.0, typed word=4.5, calc digit=5.7-17);
    RE-VALIDATE against the laptop panel's noise floor on the first physical run
    (Task 11) and adjust if the static floor differs.

    Returns "settled" | "timeout" | "no_frames" (2026-07-21, review P0-5: previously
    None in every case, so a dead capture was indistinguishable from a settled
    screen). Callers that only want the wait may ignore the return."""
    end = time.time() + max_s
    prev = None
    stable = 0
    saw_frame = False
    while time.time() < end:
        f = read_fn()
        if f is not None:
            saw_frame = True
            if prev is not None:
                if _tile_max_diff(prev, f) < thresh:
                    stable += 1
                    if stable >= stable_frames:
                        return "settled"
                else:
                    stable = 0
            prev = f
        time.sleep(poll_s)
    return "timeout" if saw_frame else "no_frames"


def make_hid_client():
    """The action channel: the Pi 5 + Pico UART appliance (the retired WiFi Pico path
    was archived 2026-07-20; see _archive/old-stack/kvm_agent/hardware/pico_client.py)."""
    return ApplianceClient()


class FrameBuffer:
    """Thread-safe latest-frame store with a monotonic sequence number.

    Finding #6 (2026-07-18 harness review): Camera._loop overwrote self.frame with no
    freshness guarantee, so a post-action verify frame could predate the action it was
    meant to check. Every stored frame gets a seq; consumers wait for seq > the seq at
    action-fire time for exact before/after pairing. get() deliberately does NOT copy:
    cv2 cap.read() returns a fresh array per call (the producer never mutates a stored
    frame), and a per-poll 6MB memcpy is real cost at settle-poll rates.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0

    def put(self, frame):
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()
            return self._seq

    def get(self):
        with self._cond:
            return self._frame, self._seq

    @property
    def seq(self):
        with self._cond:
            return self._seq

    def wait_newer(self, seq, timeout_s):
        end = time.time() + timeout_s
        with self._cond:
            while self._seq <= seq:
                remaining = end - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no frame newer than seq={seq} within {timeout_s}s")
                self._cond.wait(remaining)
            return self._frame, self._seq


# Windows target: Media Foundation (MSMF), NOT DirectShow -- the Acer USB3 card delivers
# YUY2 there and cv2's DSHOW backend mis-reads its stride and ghosts stale frames into the
# current one (the "wallpaper duplicated at two scales" artifact, 2026-06-19); OBS and MSMF
# both decode it cleanly. Linux host: V4L2 is the native/only real backend for a UVC
# capture card -- CAP_MSMF doesn't exist outside Windows.
_CAPTURE_BACKEND = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_V4L2


class Camera:
    def __init__(self, index=0, w=1920, h=1080):
        # MSMF is slow to OPEN (~20-25s one-time Media Foundation init) on Windows, hence
        # the longer first-frame wait below; once open, the threaded read drains fresh
        # frames. V4L2 on Linux opens fast by comparison.
        self.cap = cv2.VideoCapture(index, _CAPTURE_BACKEND)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._fb = FrameBuffer()
        self.run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        t0 = time.time()
        while self._fb.seq == 0:
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
                self._fb.put(f)

    @property
    def seq(self):
        """Monotonic captured-frame counter (finding #6 pairing primitive)."""
        return self._fb.seq

    def read(self):
        return self._fb.get()[0]

    def wait_newer(self, seq, timeout_s):
        """Block until a frame captured AFTER `seq` lands; TimeoutError otherwise."""
        return self._fb.wait_newer(seq, timeout_s)

    def png_bytes(self, full_res=False):
        # Downscale 1080p -> 720p before encoding: vision-token count scales with pixels
        # (measured 2026-07-17: 1/4 the pixels ~ -35% prompt tokens, and format does NOT
        # matter, only resolution) -- this is the single biggest per-step latency lever.
        # Safe for grounding because Holo outputs [0,1000] normalized coords and
        # agent_loop_holo projects them against the REAL screen size, not this PNG.
        # full_res=True skips the downscale for EVIDENCE frames (grading/verify/reference):
        # tesseract OCR on a 720p analog-capture frame produces garbage (proven 2026-07-18,
        # calc_basic's "56" unreadable) -- the model reads 720p, the graders read 1080p.
        frame, _ = self._fb.get()
        frame = frame if full_res else cv2.resize(frame, (1280, 720))
        ok, buf = cv2.imencode(".png", frame)
        return buf.tobytes()

    def model_input_jpeg(self):
        """MODEL-INPUT frame, native-style (2026-07-21, feature/native-verbatim): JPEG at
        CFG.holo_model_input_res height (1080 = native holo-desktop-cli behavior: full-res
        JPEG; 720 = the token-saving downscale, A/B-measured 2026-07-21 -- see config).
        Native transcodes screenshots to JPEG before upload (screenshot_media_type:
        image/jpeg in docs/native/*.yaml); quality 90 (native's exact quality isn't
        recoverable -- flagged in kvm_agent/models/holo.py). Aspect ratio is preserved, so
        the model's [0,1000] normalized coordinates still project against the real screen.
        NOT for evidence/grading frames (those stay full-res PNG via png_bytes)."""
        frame, _ = self._fb.get()
        h, w = frame.shape[:2]
        target_h = CFG.holo_model_input_res
        frame = frame if target_h >= h else cv2.resize(frame, (int(w * target_h / h), target_h))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
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
            # Start every session from all-keys-up: a combo interrupted mid-fault leaves the
            # modifier latched on the target, silently corrupting every later step.
            self.r4.clear_hid()
        except Exception:
            try:
                self.cam.release()   # don't orphan the capture device if HID setup fails
            except Exception:
                pass
            raise
        self.show = show
        f = self.cam.read()
        print(f"[env] capture {f.shape[1]}x{f.shape[0]}")
        if (f.shape[1], f.shape[0]) != tuple(screen_size):
            # cap.set() is a REQUEST -- V4L2 can silently fall back to another mode.
            # Coordinates project against the CONFIGURED size, so a mismatch here means
            # every click is scaled wrong until the config matches the card's real mode.
            print(f"[env] WARNING: captured {f.shape[1]}x{f.shape[0]} != configured "
                  f"{screen_size[0]}x{screen_size[1]} -- fix CFG.screen_w/h or the card mode")

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
        """Current screen as full-res PNG bytes for diffing/evidence, WITHOUT any physical
        action. Model input has its own dedicated path (Camera.model_input_jpeg, JPEG at
        CFG.holo_model_input_res) since 2026-07-21 -- this no longer doubles as the
        model-input source."""
        return {"screenshot": self.cam.png_bytes(full_res=True)}

    def close(self):
        try:
            self.r4.clear_hid()  # all keys AND buttons up, not just the mouse button
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
