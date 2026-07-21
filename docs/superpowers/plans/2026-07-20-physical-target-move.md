# Physical-Target Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the live Holo stack from the `win11-agent` libvirt VM to the physical Win10 laptop, retire the VM/WAA/shim generations per AGENTS.md §3, fix the three live harness-trust defects, and land a human-graded battery runner.

**Architecture:** Spec: `docs/PLAN_2026-07-20_physical_target_move.md` (approved, commit `4f8bf0e`). The loop (`agent_loop_holo.py`), capture (`Camera`), and HID appliance stay; `env.py` is cut down to its live surface; new seam `kvm_agent/hardware/target.py` (manual power v1); new `tools/battery.py` (human-graded). One archive commit retires all dead generations together.

**Tech Stack:** Python 3.11, cv2, numpy, PIL, openai. Tests are repo-convention **script-style** (`python tests/test_x.py`, prints `ALL PASS`, exits nonzero on failure — see `tests/test_frame_diff.py`), NOT pytest.

## Global Constraints

- Repo house rules (`AGENTS.md`): all artifacts in `runs/`; nothing project-related in hidden dirs; no ghost generations (predecessors move to `_archive/` in the SAME commit that removes them); session ends commit-or-revert with `git status` clean.
- `_archive/` is write-only: add, never extend or modify existing entries.
- Commit message style: `area: description` (see `git log --oneline`).
- Run all commands from the repo root `/home/aaron/workspace/kvm-agent`.
- Cheap gates (offline tests, imports, `py_compile`) MUST pass before any task is called done; live/rig verification is Task 11 only.
- Do NOT modify: `agent_loop_holo.py` loop logic (except the Task 5 `_execute` pairing edit), `kvm_agent/models/holo.py` (except one comment line in Task 10), appliance firmware (`appliance/pico_fw/`), `appliance/pi5/pikvm_proto.py` (except nothing — no changes at all).
- `git mv` does not move untracked/gitignored content; use shell `mv` + `git add -A` for all archive moves so gitignored artifacts travel with their code.

---

### Task 1: Archive sweep — retire the dead generations (single commit)

**Files:**
- Move: `waa/` → `_archive/old-stack/waa/`
- Move: `kvm_agent/hardware/vm.py` → `_archive/old-stack/kvm_agent/hardware/vm.py`
- Move: `tools/shakedown_ab.py`, `tools/wol.py` → `_archive/old-stack/tools/`
- Move: `appliance/pico/` → `_archive/firmware_old/appliance_pico/`
- Move: `appliance/pi5/send.py`, `appliance/host/stage2_verify.py` → `_archive/old-stack/appliance/`
- Modify: `HOLO_INTEGRATION_PLAN.md` (stamp SUPERSEDED)
- Delete (untracked): all `__pycache__/` dirs containing ghost bytecode

**Interfaces:**
- Consumes: nothing.
- Produces: a tree where no live file references `vm.py`, `waa/`, `shakedown_ab`, `wol`, `appliance/pico`, `send.py`, `stage2_verify`. (`kvm_agent/config.py` still has vm_*/dead fields — Task 9 removes them; nothing imports them after this task.)

- [ ] **Step 1: Create the branch**

```bash
cd /home/aaron/workspace/kvm-agent
git checkout -b feature/physical-target-move
```

- [ ] **Step 2: Delete ghost bytecode and stage the moves**

```bash
cd /home/aaron/workspace/kvm-agent
rm -rf __pycache__ tools/__pycache__ tests/__pycache__ appliance/pico/__pycache__ .pytest_cache
mkdir -p _archive/old-stack/kvm_agent/hardware _archive/old-stack/tools _archive/old-stack/appliance
mv waa _archive/old-stack/waa
mv kvm_agent/hardware/vm.py _archive/old-stack/kvm_agent/hardware/vm.py
mv tools/shakedown_ab.py tools/wol.py _archive/old-stack/tools/
mv appliance/pico _archive/firmware_old/appliance_pico
mv appliance/pi5/send.py appliance/host/stage2_verify.py _archive/old-stack/appliance/
rmdir appliance/host 2>/dev/null || true
```

- [ ] **Step 3: Stamp HOLO_INTEGRATION_PLAN.md SUPERSEDED**

Insert at the very top of `HOLO_INTEGRATION_PLAN.md`, before the existing first line:

```markdown
> **SUPERSEDED 2026-07-20.** Phases I0–I5 are done and the topology this plan describes
> (WiFi Pico + r4_client, DSHOW-on-Windows capture) was retired in the 2026-07-20 sweep.
> Current plan: docs/PLAN_2026-07-20_physical_target_move.md. Kept for history.
```

- [ ] **Step 4: Verify no live references remain**

Run: `grep -rn "hardware.vm\|import vm\|waa/\|shakedown_ab\|wol\b" --include="*.py" kvm_agent/ tools/ tests/ agent_loop_holo.py`
Expected: no output (zero matches). Also `python -c "import agent_loop_holo"` must exit 0, and `python tests/test_frame_diff.py` must print `ALL PASS`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "repo: retire VM/WAA/WiFi-Pico generations to _archive (physical-target move)"
```

---

### Task 2: Excise the EvoCUA exec-shim from env.py

**Files:**
- Preserve: copy current `kvm_agent/hardware/env.py` → `_archive/old-stack/kvm_agent/hardware/env.py` (same commit, per §3)
- Modify: `kvm_agent/hardware/env.py` (rewrite to live surface only)

**Interfaces:**
- Consumes: Task 1's tree.
- Produces: `kvm_agent.hardware.env` exporting `wait_until_stable`, `make_hid_client`, `Camera`, `PicoEnv` with attributes `.cam`, `.r4` and methods `.observe() -> {"screenshot": bytes}`, `._settle(secs)`, `.close()`. `agent_loop_holo.py` uses ONLY `PicoEnv(cam_index=, screen_size=, show=)`, `ENV.observe()["screenshot"]`, `ENV.cam.read/png_bytes/release`, `ENV.r4.*` — all preserved.
- REMOVED (archive copy keeps them): `PicoPyAutoGUI`, `PicoController`, `PicoEnv.step/reset/evaluate/end_full_png`, `action_history`, `instruction`, `reset_coord/reset_settle` params. Grep-verified: no live consumer (`end_full_png`/`.controller` appear only inside env.py itself).

- [ ] **Step 1: Preserve the shim in the archive**

```bash
cp kvm_agent/hardware/env.py _archive/old-stack/kvm_agent/hardware/env.py
```

- [ ] **Step 2: Rewrite `kvm_agent/hardware/env.py` as exactly this**

```python
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
```

Then append the **current** `Camera` class verbatim (copy `kvm_agent/hardware/env.py`'s existing `class Camera` block — the archive copy made in Step 1 is the reference), followed by this `PicoEnv`:

```python
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
```

- [ ] **Step 3: Verify**

Run: `python -c "import agent_loop_holo"` — expected exit 0.
Run: `python tests/test_frame_diff.py` — expected `ALL PASS`.
Run: `grep -n "PicoPyAutoGUI\|PicoController\|execute_python_command\|def step\|def reset\|def evaluate\|end_full_png" kvm_agent/hardware/env.py` — expected no output.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "env: excise archived EvoCUA exec-shim; keep live capture/HID surface"
```

---

### Task 3: Wire `clear_hid` end-to-end (all-keys-up on connect and close)

**Files:**
- Modify: `appliance/pi5/hid_bridge.py` (add `/hid/clear` route)
- Modify: `kvm_agent/hardware/appliance.py:82-90` (add `clear_hid()`)
- Modify: `kvm_agent/hardware/env.py` (call on connect + in `close()`)
- Test: `tests/test_clear_hid.py`

**Interfaces:**
- Consumes: `pikvm_proto.PicoHidLink.clear_hid()` (already exists, `pikvm_proto.py:177` — CMD_CLEAR_HID roundtrip; NO proto changes).
- Produces: `ApplianceClient.clear_hid() -> dict` (raises `ApplianceError` on failure); bridge route `POST /hid/clear` returning ack `"CLR"`; `PicoEnv.__init__` and `PicoEnv.close()` both call `r4.clear_hid()`.

- [ ] **Step 1: Write the failing test `tests/test_clear_hid.py`**

```python
"""
test_clear_hid.py — OFFLINE test: ApplianceClient.clear_hid() hits the bridge's
/hid/clear route and raises loudly on a not-ok response (the all-keys-up wiring).

    python tests/test_clear_hid.py
"""
import sys, os, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from http.server import BaseHTTPRequestHandler, HTTPServer

from kvm_agent.hardware.appliance import ApplianceClient, ApplianceError

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

hits = []

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        hits.append(self.path)
        ok = self.path == "/hid/clear"
        body = json.dumps({"ok": ok, "ack": "CLR" if ok else "no such route"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}"

ApplianceClient(base_url=url).clear_hid()
check("clear_hid posts to /hid/clear", hits == ["/hid/clear"])

try:
    ApplianceClient(base_url=url)._req("/hid/bogus")
    check("not-ok response raises ApplianceError", False)
except ApplianceError:
    check("not-ok response raises ApplianceError", True)

srv.shutdown()
print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_clear_hid.py`
Expected: FAIL — `ApplianceError` from `clear_hid` not existing (`AttributeError`), i.e. the script errors before `ALL PASS`.

- [ ] **Step 3: Add the client method**

In `kvm_agent/hardware/appliance.py`, in the `# --- appliance-specific ---` section above `probe()`:

```python
    def clear_hid(self):
        """All-keys-up: release every held key/button on the target. Called on connect
        and on close() so a mid-fault latched modifier (combo interrupted by a link
        failure) can't corrupt the next session's input state."""
        return self._req("/hid/clear")
```

- [ ] **Step 4: Add the bridge route**

In `appliance/pi5/hid_bridge.py`, next to `_cmd_probe`:

```python
def _cmd_clear(q):
    LINK.clear_hid()
    return "CLR"
```

and add `"/hid/clear": _cmd_clear,` to `ROUTES`. Also add the line
`  POST /hid/clear                  -> all-keys-up (release every held key/button)`
to the module docstring's route list.

- [ ] **Step 5: Call it on connect and close in env.py**

In `PicoEnv.__init__`, immediately after `self.r4 = make_hid_client()` succeeds (after the try/except block):

```python
        # Start every session from all-keys-up: a combo interrupted mid-fault leaves the
        # modifier latched on the target, silently corrupting every later step.
        self.r4.clear_hid()
```

In `PicoEnv.close()`, replace the `self.r4.up()` block with:

```python
        try:
            self.r4.clear_hid()  # all keys AND buttons up, not just the mouse button
        except Exception:
            pass
```

- [ ] **Step 6: Run tests**

Run: `python tests/test_clear_hid.py` — expected `ALL PASS`.
Run: `python -m py_compile appliance/pi5/hid_bridge.py` — expected exit 0 (the route itself is live-verified in Task 11's smoke test; pyserial is a Pi-side dep, not necessarily present on the host).
Run: `python -c "import agent_loop_holo"` — expected exit 0.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "hid: wire clear_hid end-to-end (bridge route, client, connect+close)"
```

---

### Task 4: Frame freshness — FrameBuffer with monotonic sequence numbers

**Files:**
- Modify: `kvm_agent/hardware/env.py` (add `FrameBuffer`, rewrite `Camera` to use it)
- Test: `tests/test_frame_buffer.py`

**Interfaces:**
- Consumes: nothing outside env.py.
- Produces: `FrameBuffer` with `put(frame) -> int`, `get() -> (frame, seq)`, `.seq -> int`, `wait_newer(seq, timeout_s) -> (frame, seq)` (raises `TimeoutError`). `Camera` keeps its public surface (`read()`, `png_bytes(full_res=)`, `release()`) and gains `.seq` and `.wait_newer(seq, timeout_s)`. Task 5's `_execute` edit consumes `ENV.cam.seq` / `ENV.cam.wait_newer`.

- [ ] **Step 1: Write the failing test `tests/test_frame_buffer.py`**

```python
"""
test_frame_buffer.py — OFFLINE test for the frame-freshness store (finding #6:
no guarantee a post-action frame was captured after the action).

    python tests/test_frame_buffer.py
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import FrameBuffer

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

fb = FrameBuffer()
check("empty buffer seq 0, frame None", fb.seq == 0 and fb.get() == (None, 0))

f1 = np.zeros((4, 4, 3), np.uint8)
s1 = fb.put(f1)
check("put returns seq 1", s1 == 1)
got, gseq = fb.get()
check("get returns latest + seq", gseq == 1 and got is f1)

f2 = np.ones((4, 4, 3), np.uint8)
fb.put(f2)
check("seq advances monotonically", fb.seq == 2 and fb.get()[0] is f2)

# wait_newer returns promptly once a newer frame lands (producer on another thread)
fb2 = FrameBuffer()
fb2.put(f1)
threading.Timer(0.05, lambda: fb2.put(f2)).start()
t0 = time.time()
frame, seq = fb2.wait_newer(1, timeout_s=2.0)
check("wait_newer returns the newer frame", seq == 2 and frame is f2 and time.time() - t0 < 1.0)

# wait_newer times out loudly when nothing newer arrives
try:
    fb2.wait_newer(99, timeout_s=0.1)
    check("wait_newer times out with TimeoutError", False)
except TimeoutError:
    check("wait_newer times out with TimeoutError", True)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_frame_buffer.py`
Expected: `ImportError`/`ImportError: cannot import name 'FrameBuffer'`.

- [ ] **Step 3: Add FrameBuffer and rewire Camera in env.py**

Add after `make_hid_client` (before `_CAPTURE_BACKEND`):

```python
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
```

Replace the entire `Camera` class with:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python tests/test_frame_buffer.py` — expected `ALL PASS`.
Run: `python -c "import agent_loop_holo"` and `python tests/test_frame_diff.py` — expected exit 0 / `ALL PASS`.
Run: `grep -n "self\.frame" kvm_agent/hardware/env.py` — expected no output (the bare attribute is gone; everything goes through `_fb`).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "capture: FrameBuffer seq/timestamp store — exact before/after pairing (finding #6)"
```

---

### Task 5: Pair post-action verification with the action in `_execute`

**Files:**
- Modify: `agent_loop_holo.py:211-216` (the `_execute` fire/settle block)

**Interfaces:**
- Consumes: `ENV.cam.seq`, `ENV.cam.wait_newer(seq, timeout_s)` from Task 4.
- Produces: unchanged signatures; `_execute` now guarantees the observation pipeline advanced past the fire before settling/verifying.

- [ ] **Step 1: Edit `_execute` in agent_loop_holo.py**

Replace exactly:

```python
    verifiable = kind in ("left_click", "type")
    before = _frame_png() if verifiable else None

    _fire()
    # Smart settle (2026-07-18): proceed the moment the UI stops changing, up to settle_s.
    wait_until_stable(ENV.cam.read, settle_s)
```

with:

```python
    verifiable = kind in ("left_click", "type")
    before = _frame_png() if verifiable else None
    seq0 = ENV.cam.seq

    _fire()
    # Finding #6 pairing: guarantee the capture pipeline has advanced PAST the fire
    # before settling, so the `after` frame can never be one captured before the action
    # landed. (A fresh frame can still predate the visible EFFECT; wait_until_stable
    # covers settling on top of this freshness floor.)
    try:
        ENV.cam.wait_newer(seq0, timeout_s=settle_s)
    except TimeoutError:
        print(f"[execute] WARNING: capture stalled — no frame newer than seq={seq0} "
              f"within {settle_s}s")
    # Smart settle (2026-07-18): proceed the moment the UI stops changing, up to settle_s.
    wait_until_stable(ENV.cam.read, settle_s)
```

- [ ] **Step 2: Verify**

Run: `python -c "import agent_loop_holo"` — expected exit 0.
Run: `python tests/test_frame_diff.py` and `python tests/test_frame_buffer.py` — expected `ALL PASS` each.
Behavioral verification is Task 11 (live); there is no offline harness for `_execute` (it needs camera + appliance).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "holo: pair _execute verify frames with the action (wait_newer floor)"
```

---

### Task 6: Tile-max settle metric in `wait_until_stable`

**Files:**
- Modify: `kvm_agent/hardware/env.py` (replace `wait_until_stable`, add `_tile_max_diff`)
- Test: `tests/test_settle.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `wait_until_stable(read_fn, max_s, stable_frames=3, thresh=3.0, poll_s=0.05)` — same signature except `thresh` default changes 2.0 → 3.0 and the metric becomes tile-max. Callers (`PicoEnv._settle`, `agent_loop_holo._execute` via `ENV.cam.read`) need no changes.

- [ ] **Step 1: Write the failing test `tests/test_settle.py`**

```python
"""
test_settle.py — OFFLINE test: wait_until_stable uses the tile-max metric, so a
small LOCALIZED change (calc-digit class, flaw #4) counts as "still changing" while
uniform low-level noise counts as stable.

    python tests/test_settle.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from kvm_agent.hardware.env import wait_until_stable

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

def scripted(frames):
    it = iter(frames)
    last = [frames[-1]]
    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return read

BASE = np.full((270, 480, 3), 128, np.uint8)

# (a) truly stable sequence -> returns well before max_s
t0 = time.time()
wait_until_stable(scripted([BASE.copy() for _ in range(50)]), max_s=2.0, poll_s=0.005)
check("stable sequence settles fast", time.time() - t0 < 1.0)

# (b) small localized change every poll (a 40x40 block toggling) -> NOT stable,
#     must burn the whole window (the case the whole-frame mean missed)
churn = []
for i in range(50):
    f = BASE.copy()
    if i % 2:
        f[100:140, 200:240] = 255
    churn.append(f)
t0 = time.time()
wait_until_stable(scripted(churn), max_s=0.4, poll_s=0.005)
check("localized churn never reads as stable", time.time() - t0 >= 0.35)

# (c) uniform +1 noise everywhere -> below threshold, reads as stable
noise = [(BASE.astype(int) + (i % 2)).clip(0, 255).astype(np.uint8) for i in range(50)]
t0 = time.time()
wait_until_stable(scripted(noise), max_s=2.0, poll_s=0.005)
check("uniform low-level noise reads as stable", time.time() - t0 < 1.0)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_settle.py`
Expected: FAIL on check (b) — the current whole-frame-mean metric reads the toggling block as stable (diff ~3.7 on a 160×90 mean is above 2.0... if it unexpectedly passes, tighten by shrinking the block to 20×20 so the old metric definitely misses it: mean diff of a 20×20 toggle at 160×90 downscale ≈ 0.3–1.1, below 2.0).

- [ ] **Step 3: Replace `wait_until_stable` in env.py**

Replace the whole current `wait_until_stable` function with:

```python
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
    (Task 11) and adjust if the static floor differs."""
    end = time.time() + max_s
    prev = None
    stable = 0
    while time.time() < end:
        f = read_fn()
        if f is not None:
            if prev is not None:
                if _tile_max_diff(prev, f) < thresh:
                    stable += 1
                    if stable >= stable_frames:
                        return
                else:
                    stable = 0
            prev = f
        time.sleep(poll_s)
```

- [ ] **Step 4: Run tests**

Run: `python tests/test_settle.py` — expected `ALL PASS`.
Run: `python -c "import agent_loop_holo"`, `python tests/test_frame_diff.py`, `python tests/test_frame_buffer.py` — expected exit 0 / `ALL PASS` / `ALL PASS`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "settle: tile-max metric in wait_until_stable (flaw #4 metric fully retired)"
```

---

### Task 7: Physical-target seam — `kvm_agent/hardware/target.py` (manual v1)

**Files:**
- Create: `kvm_agent/hardware/target.py`
- Test: `tests/test_target.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `target.reboot() -> None` (blocks until the operator confirms the desktop is up) and `target.is_up() -> bool`. `tools/battery.py` (Task 8) consumes `target.reboot`. Future `wol`/`smartplug` backends keep these two signatures.

- [ ] **Step 1: Write the failing test `tests/test_target.py`**

```python
"""
test_target.py — OFFLINE test for the manual power/reset seam.

    python tests/test_target.py
"""
import sys, os, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.hardware import target

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

calls = []
real_input = builtins.input
builtins.input = lambda prompt="": calls.append(prompt) or ""
try:
    target.reboot()
finally:
    builtins.input = real_input
check("reboot() blocks on operator confirmation exactly once", len(calls) == 1)
check("reboot() prompt tells the operator what to do", "power-cycle" in calls[0].lower())
check("is_up() is True after operator confirmation (v1 contract)", target.is_up() is True)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_target.py`
Expected: `ModuleNotFoundError: No module named 'kvm_agent.hardware.target'`.

- [ ] **Step 3: Create `kvm_agent/hardware/target.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_target.py` — expected `ALL PASS`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "target: manual power/reset seam for the physical laptop (v1)"
```

---

### Task 8: Human-graded battery runner — `tools/battery.py`

**Files:**
- Create: `tools/battery.py`
- Create: `tools/battery_tasks_shakedown.json`
- Test: `tests/test_battery.py`

**Interfaces:**
- Consumes: `agent_loop_holo.boot/run/shutdown` (run returns `{"finished": bool, "answer_text": str}`), `kvm_agent.hardware.target.reboot` (Task 7), `CFG.runs_dir`.
- Produces: `load_tasks(path) -> list[dict]` (validated, `max_steps` defaulted to 15), `grade_task(task, result) -> {"grade": "pass"|"fail", "note": str}`, `write_results(path, payload)`. Artifacts: per-task `runs/battery_<task_id>_<ts>/` (via RunRecorder inside run()) and `runs/battery_<ts>_results.json`.

- [ ] **Step 1: Write the failing test `tests/test_battery.py`**

```python
"""
test_battery.py — OFFLINE test for the battery runner's pure parts (task loading,
grading input, results writing). The interactive runner itself is live-verified.

    python tests/test_battery.py
"""
import sys, os, json, tempfile, builtins
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import battery

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)

with tempfile.TemporaryDirectory() as td:
    good = os.path.join(td, "tasks.json")
    with open(good, "w") as f:
        json.dump([{"id": "t1", "instruction": "do thing"}], f)
    tasks = battery.load_tasks(good)
    check("load_tasks returns tasks", len(tasks) == 1 and tasks[0]["id"] == "t1")
    check("max_steps defaults to 15", tasks[0]["max_steps"] == 15)

    bad = os.path.join(td, "bad.json")
    with open(bad, "w") as f:
        json.dump([{"instruction": "no id"}], f)
    try:
        battery.load_tasks(bad)
        check("task without id rejected", False)
    except AssertionError:
        check("task without id rejected", True)

    out = os.path.join(td, "results.json")
    battery.write_results(out, {"results": [], "score": "0/0"})
    with open(out) as f:
        check("write_results round-trips", json.load(f)["score"] == "0/0")

# grading: empty input re-asks (a grade can never be silently recorded, finding #8);
# 'p note' -> pass with note; 'f' -> fail with empty note
answers = iter(["", "p looks good"])
real_input = builtins.input
builtins.input = lambda prompt="": next(answers)
try:
    v = battery.grade_task({"id": "t1"}, {"finished": True, "answer_text": ""})
finally:
    builtins.input = real_input
check("grade_task re-asks on empty, then passes", v == {"grade": "pass", "note": "looks good"})

answers = iter(["f fell over"])
builtins.input = lambda prompt="": next(answers)
try:
    v = battery.grade_task({"id": "t2"}, {"finished": False, "answer_text": ""})
finally:
    builtins.input = real_input
check("grade_task fail with note", v == {"grade": "fail", "note": "fell over"})

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_battery.py`
Expected: `ModuleNotFoundError: No module named 'battery'`.

- [ ] **Step 3: Create `tools/battery.py`**

```python
#!/usr/bin/env python3
"""
battery.py — human-graded task battery for the physical target
(docs/PLAN_2026-07-20_physical_target_move.md §5).

Per task: operator reboots the laptop (target.reboot) -> the Holo loop runs with full
RunRecorder instrumentation -> the operator grades pass/fail from the final frame +
run artifacts. NO automated grading at this stage: the user is the grader, and no
None/uncertain grade can ever masquerade as a pass (finding #8 — fail-open grading is
the anti-pattern this project exists to kill).

    python tools/battery.py tools/battery_tasks_shakedown.json

Artifacts (AGENTS.md §1 — everything under runs/):
    runs/battery_<task_id>_<ts>/    per-task RunRecorder dirs (written by run())
    runs/battery_<ts>_results.json  grades + provenance for the whole battery
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware import target
from agent_loop_holo import boot, run, shutdown


def load_tasks(path):
    """Read + validate the task list. Each task: {"id", "instruction",
    "max_steps" (optional, default 15), "setup" (optional operator note)}."""
    with open(path) as f:
        tasks = json.load(f)
    assert isinstance(tasks, list) and tasks, "task file must be a non-empty JSON list"
    for t in tasks:
        assert isinstance(t.get("id"), str) and t["id"], f"task missing id: {t!r}"
        assert isinstance(t.get("instruction"), str) and t["instruction"], \
            f"task {t.get('id')!r} missing instruction"
        t.setdefault("max_steps", 15)
    return tasks


def grade_task(task, result):
    """The human grader. No default and no empty answer — a grade can never be
    silently recorded (finding #8). Input form: 'p <optional note>' / 'f <optional note>'."""
    while True:
        raw = input(f"[battery] task {task['id']!r}: grade [p/f] + optional note: ").strip()
        if raw[:1] in ("p", "f"):
            return {"grade": "pass" if raw[0] == "p" else "fail", "note": raw[1:].strip()}
        print("[battery] need 'p' or 'f' — no grade, no continue")


def write_results(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[battery] results -> {path}")


def main():
    tasks_path = sys.argv[1] if len(sys.argv) > 1 else "tools/battery_tasks_shakedown.json"
    tasks = load_tasks(tasks_path)
    ts = time.strftime("%Y%m%d_%H%M%S")
    print(f"[battery] {len(tasks)} tasks from {tasks_path}")
    print("[battery] REMINDER: start Steps Recorder (psr.exe) on the laptop (raise its "
          "100-capture cap in its settings first) and drop its .zip into the battery's "
          "run dirs afterward — it is the independent ground-truth channel.")
    boot()
    results = []
    for i, task in enumerate(tasks):
        print(f"\n[battery] === task {i + 1}/{len(tasks)}: {task['id']} ===")
        if task.get("setup"):
            print(f"[battery] setup: {task['setup']}")
        target.reboot()
        tag = f"battery_{task['id']}"
        # no_progress_abort=False per H1 (2026-07-19): the frozen-screen/same-click
        # aborts fired falsely on recoverable tasks; benchmark runs give the full budget.
        result = run(task["instruction"], max_steps=task["max_steps"],
                     confirm_first=0, tag=tag, no_progress_abort=False)
        verdict = grade_task(task, result)
        results.append({"task_id": task["id"], "instruction": task["instruction"],
                        "run_tag": tag, "finished": result["finished"],
                        "answer_text": result["answer_text"], "grader": "human", **verdict})
        print(f"[battery] {task['id']}: {verdict['grade']} ({verdict['note']})")
    shutdown()
    payload = {"started": ts, "tasks_file": tasks_path, "results": results,
               "score": f"{sum(r['grade'] == 'pass' for r in results)}/{len(results)}"}
    write_results(os.path.join(CFG.runs_dir, f"battery_{ts}_results.json"), payload)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `tools/battery_tasks_shakedown.json`**

```json
[
  {"id": "notepad_type", "instruction": "Open Notepad and type the sentence: the quick brown fox jumps over the lazy dog", "max_steps": 15},
  {"id": "calc_multiply", "instruction": "Open Calculator and compute 7 times 8", "max_steps": 20},
  {"id": "settings_display", "instruction": "Open the Settings app and go to the Display settings page", "max_steps": 15},
  {"id": "paint_line", "instruction": "Open Paint and draw a straight line across the canvas", "max_steps": 20},
  {"id": "taskbar_clock", "instruction": "Read the current time shown on the taskbar clock and tell me what it says", "max_steps": 10}
]
```

- [ ] **Step 5: Run tests**

Run: `python tests/test_battery.py` — expected `ALL PASS`.
Run: `python -m py_compile tools/battery.py` — expected exit 0.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "battery: human-graded task runner for the physical target"
```

---

### Task 9: Config / llm / packaging dead-weight removal

**Files:**
- Modify: `kvm_agent/config.py` (rewrite — drop dead fields)
- Modify: `kvm_agent/__init__.py` (drop env seeding)
- Modify: `kvm_agent/llm/ollama.py` (drop `ollama_generate`, fix docstring + fallbacks)
- Modify: `tools/show_reasoning.py:96-98` (fix stale `CFG.planner_thinking` pointer)
- Modify: `pyproject.toml` (deps to live set)
- Modify: `.gitignore` (drop stale evocua/waa entries)

**Interfaces:**
- Consumes: Task 1's tree (the archived consumers of these fields are gone).
- Produces: `CFG` with ONLY: `appliance_url`, `cam_index`, `screen_w`, `screen_h`, `runs_dir`, `holo_local_url`, `holo_hosted_url`, `holo_model`, `holo_hosted_model`, `hai_api_key`, `holo_history_images`, `holo_model_input_full_res`, `screen_size`. `openai_client(base_url, api_key, timeout)` with env-var (not CFG) fallbacks — its one live caller (`holo.py:457`) passes both explicitly.

- [ ] **Step 1: Rewrite `kvm_agent/config.py` as exactly this**

```python
"""Central configuration for the KVM-over-IP agent.

Every IP, port, endpoint, model name, and screen dim the rig uses lives HERE.
Override any field via the matching environment variable.

2026-07-20: the retired stack's fields (WiFi Pico, libvirt VM, Ollama verifier,
planner, hindsight, closed-loop, tesseract) were removed in the physical-target
sweep — their only consumers are in _archive/old-stack/. History: git log.
"""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_local_env():
    """Load KEY=VALUE lines from .env.local (repo root, gitignored) into os.environ,
    without overriding anything already set. Keeps secrets (e.g. HAI_API_KEY) out of
    source while requiring no new dependency."""
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_local_env()


def _env(key, default):
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Config:
    # --- HID action channel: Pi 5 + Pico over wired UART + HTTP bridge ---
    appliance_url: str = _env("APPLIANCE_URL", "http://192.168.0.29:8080")  # Pi 5 hid_bridge
    cam_index: int = int(_env("CAM_INDEX", "0"))
    screen_w: int = int(_env("SCREEN_W", "1920"))
    screen_h: int = int(_env("SCREEN_H", "1080"))

    # --- orchestration / IO: all run artifacts live under runs/ (AGENTS.md §1) ---
    runs_dir: str = _env("RUNS_DIR", str(Path(__file__).resolve().parent.parent / "runs"))

    # --- Holo3.1 grounding model (llama.cpp SYCL on the Arc Pro B70, port 9292;
    #     hosted reference API for local-vs-hosted diffing) ---
    holo_local_url: str = _env("HOLO_LOCAL_URL", "http://127.0.0.1:9292/v1")
    holo_hosted_url: str = _env("HOLO_HOSTED_URL", "https://api.hcompany.ai/v1")
    holo_model: str = _env("HOLO_MODEL", "holo3.1")
    holo_hosted_model: str = _env("HOLO_HOSTED_MODEL", "holo3-1-35b-a3b")
    hai_api_key: str = _env("HAI_API_KEY", "")   # hosted Holo API credential ("" -> unset)
    # "goldfish memory" (2026-07-18): screenshots kept in the agent_loop_holo.py history,
    # evicting older frames to text. Each kept screenshot re-pays its vision tokens on
    # EVERY step, and text history already carries the narrative.
    holo_history_images: int = int(_env("HOLO_HISTORY_IMAGES", "1"))
    # Model-input capture resolution. Default False = 720p downscale (-35% prompt tokens,
    # measured 2026-07-17 with no grounding cost on LARGE, sparse targets). 2026-07-19: a
    # dense calendar date-picker showed real coordinate misses at 720p that a native
    # 1080p-fed run did not reproduce -- test via HOLO_MODEL_INPUT_FULL_RES=1 on dense UIs.
    holo_model_input_full_res: bool = _env("HOLO_MODEL_INPUT_FULL_RES", "0") != "0"

    @property
    def screen_size(self):
        return (self.screen_w, self.screen_h)


CFG = Config()
```

- [ ] **Step 2: Rewrite `kvm_agent/__init__.py` as exactly this**

```python
"""kvm_agent - consolidated KVM-over-IP computer-use agent."""
```

(The `OPENAI_BASE_URL`/`OPENAI_API_KEY` env seeding existed for archived EvoCUA/UI-TARS consumers; nothing live reads those env vars — holo.py passes base_url explicitly.)

- [ ] **Step 3: Rewrite `kvm_agent/llm/ollama.py` as exactly this**

```python
"""Shared OpenAI-compatible client factory.

One cached client per (base_url, key, timeout) instead of constructing one per call.
The only live consumer is kvm_agent.models.holo (holo.py:457, which passes base_url
and api_key explicitly). The Ollama /api/generate helper this module also carried was
archived 2026-07-20 with the Ollama-based verifier; the filename survives to keep the
import path stable.
"""
import os
from functools import lru_cache


@lru_cache(maxsize=8)
def openai_client(base_url: str = None, api_key: str = None, timeout: float = 180.0):
    """Cached OpenAI-compatible client (one per distinct (base_url, key, timeout))."""
    import openai
    return openai.OpenAI(base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
                         api_key=api_key or os.environ.get("OPENAI_API_KEY", "unused"),
                         timeout=timeout)
```

- [ ] **Step 4: Fix the stale pointer in `tools/show_reasoning.py`**

Read lines 90–100 of `tools/show_reasoning.py`. The missing-reasoning warning references `CFG.planner_thinking` (a retired-planner knob, deleted in Step 1). Rewrite the warning so it points at the live knob: the `enable_thinking` parameter of `call_holo_full` in `kvm_agent/models/holo.py`. Keep the rest of the warning text intact.

- [ ] **Step 5: Rewrite `pyproject.toml` deps and drop the stale scripts comment**

Replace the `dependencies` list and trailing comment block with:

```toml
dependencies = [
  "opencv-python",
  "numpy",
  "pillow",
  "openai",
  "requests",
]
```

and replace the trailing `# console_scripts are wired at cutover...` comment and commented `[project.scripts]` with:

```toml
# NOTE: the live entry point agent_loop_holo.py is a repo-root script, not part of the
# package — `pip install .` delivers the library only. Known packaging gap (2026-07-20).
```

(Removed deps — `anthropic`, `backoff`, `fastapi`, `uvicorn`, `pytesseract`, `huggingface_hub` — are imported only in `_archive/`; `requests` is imported by live code.)

- [ ] **Step 6: Clean stale .gitignore entries**

Read `.gitignore`. Remove the `evocua/` entry with its "Delete evocua/ once the vendored path is rig-verified" comment (both are gone) and the now-unreachable `waa/cache`, `waa/results`, `waa/shakedown_results` entries (waa/ moved to `_archive/` in Task 1). Leave everything else.

- [ ] **Step 7: Verify zero live references to deleted fields**

Run:
```bash
grep -rn "pico_ip\|pico_port\|hid_kind\|ollama_base\|openai_base\|openai_key\|executor_model\|verifier_model\|verifier_local_model\|verifier_max_tokens\|planner_\|closed_loop\|tesseract_cmd\|hindsight_\|anthropic_key\|vm_domain\|vm_reset\|vm_snapshot\|vm_revert_settle\|vm_boot_wait\|ollama_generate" --include="*.py" kvm_agent/ tools/ tests/ agent_loop_holo.py
```
Expected: no output.
Run: `python -c "import agent_loop_holo"` — exit 0. Run all four offline tests (`test_frame_diff`, `test_frame_buffer`, `test_settle`, `test_target`, `test_battery`, `test_clear_hid`) — all `ALL PASS`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "config: drop retired-stack fields; slim deps to the live set"
```

---

### Task 10: Docs close-out (PROJECT_STATE, CLAUDE.md header, stale comments)

**Files:**
- Modify: `PROJECT_STATE.md` (rewrite)
- Modify: `CLAUDE.md` (two header corrections only)
- Modify: `agent_loop_holo.py:7-9` (STATUS line), `kvm_agent/instrumentation/run_log.py:3` (ghost citation), `kvm_agent/models/holo.py:133` (stale backing comment)
- Create: `docs/SESSION_2026-07-20_physical_target_move.md` (filled in during/after Task 11; create as skeleton now)

**Interfaces:**
- Consumes: all previous tasks.
- Produces: docs matching reality per AGENTS.md §4 (session close-out).

- [ ] **Step 1: Rewrite `PROJECT_STATE.md` as exactly this**

```markdown
# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-07-20 — physical-target move. Supersedes the 2026-07-20 post-sweep
snapshot (git history). Design: `docs/PLAN_2026-07-20_physical_target_move.md`._

## 1. What it is

A computer-use agent where **nothing is installed on the target**. A local vision
model sees the target's screen over an HDMI capture card and drives it through a
physical USB-HID injector. The target sees only a monitor + USB mouse/keyboard —
OS-agnostic, undetectable. Pure curiosity project.

## 2. The live system (current iteration)

- **LOOP** — `agent_loop_holo.py`: one tool-call per step, observe→act with
  verify-and-retry (paired to the action via frame seq numbers). Model: **Holo3.1-35B**
  served locally via **llama-swap** (`http://127.0.0.1:9292/v1`, SYCL llama-server on
  the Arc Pro B70, modelctl-managed).
- **HID** — Pi 5 + Pico 2 W **appliance** (`appliance/`): Pico runs `pico_fw/`
  (C/TinyUSB, PiKVM port, CRC16 binary protocol over 3-wire UART); Pi 5 runs
  `hid_bridge.py` (HTTP API, `http://192.168.0.29:8080`). Host client:
  `kvm_agent/hardware/appliance.py`. `clear_hid` (all-keys-up) runs on connect + close.
- **CAPTURE** — HDMI capture card via cv2 (V4L2 on the Linux host), `Camera` +
  `FrameBuffer` (monotonic frame seq) in `kvm_agent/hardware/env.py`.
- **TARGET** — physical **Windows 10 spare laptop**, lid closed, HDMI out → capture
  card → passthrough to the user's monitor. Power/reset seam:
  `kvm_agent/hardware/target.py` (v1 MANUAL reboot; WoL/smart-plug backend deferred —
  decide with hardware in front of us). Reset strategy: reboot between tasks; disk
  image (Clonezilla) as the determinism backstop.
- **EVAL** — human-graded battery: `tools/battery.py` + task JSON. The user grades
  pass/fail per task from the recorded evidence; no automated grade exists and no
  uncertain grade can masquerade as a pass (finding #8). Steps Recorder (psr.exe) on
  the laptop is the independent ground-truth channel (what Windows actually received
  vs what the capture card saw).
- **EVIDENCE** — every run records per-step frames + raw model output +
  `reasoning_content` to `runs/<tag>_<time>/` (`RunRecorder`). First tool on any
  failed run: `tools/show_reasoning.py`.

## 3. Solved (verified)

- Win32 focus-transfer bug (2026-07-19, click-to-focus retry in `_execute()`).
- WAA server terminal-window leak (patched + re-baked; moot post-WAA).
- Pico HID reliability (PiKVM firmware port; WiFi-Pico path retired).
- Harness trust (2026-07-20): tile-max settle metric, frame-seq before/after pairing
  (finding #6 closed), `clear_hid` wiring.
- Blame ledger: **model 0, our code 3** (`AGENTS.md` §5).

## 4. Open problems

- **First honest baseline**: the physical shakedown battery (5 tasks,
  `tools/battery_tasks_shakedown.json`) has not yet run — all prior numbers came from
  the VM stack and don't transfer.
- windows_calc class: WinUI3 date-picker inconsistency + stuck-popup click bug
  (2026-07-19 session doc §4). Win10's classic calc may not reproduce it — re-observe.
- Settle threshold (3.0) is calibrated on the VM-era capture chain; re-validate on
  the laptop panel's noise floor on the first physical run.
- Store auto-update pause expiry (VM-era note; re-assess for the laptop).
- Deferred: power-control backend, firmware HID watchdog, automated fail-closed
  vision grading (schema slot exists: `grader` field in battery results).

## 5. Retired

2026-07-20 sweep: EvoCUA/UI-TARS/B580-planner stack, orchestration, battery-v1,
hindsight, Ollama verifier, WiFi Pico, CircuitPython firmware, rig/preflight.
2026-07-20 physical move: **libvirt VM stack (`vm.py`, win11-agent),
WindowsAgentArena (`waa/`), the EvoCUA pyautogui exec-shim, `wol.py`,
`shakedown_ab.py`, `appliance/pico/` + `send.py` + `stage2_verify.py`** — all in
`_archive/`. Nothing live imports from `_archive/`.

## 6. House rules

`AGENTS.md` is law for every agent: all artifacts in `runs/`, nothing in hidden
dirs, the model is the last suspect, no ghost generations, sessions end
commit-or-revert with this file updated.
```

- [ ] **Step 2: Fix the two CLAUDE.md header defects**

Read `CLAUDE.md` lines 1–80. Two corrections, nothing else:
1. In the "Repo layout (cleaned 2026-07-20)" block, the line describing `kvm_agent/` lists `orchestration, server` as active subpackages — replace with the live list (config, hardware, instrumentation, llm, models).
2. In the top banner (the 2026-07-19 block claiming "WORKING TREE HAS REAL, TESTED, UNCOMMITTED CHANGES"), append a dated note that those changes were committed (`00efc76`, `61f0ca6`, `69b603d`) and the tree is clean as of the 2026-07-20 physical-move work.

- [ ] **Step 3: Fix three stale comments in live code**

1. `agent_loop_holo.py:7-9` STATUS line: replace "verified live against the rig (VM target, SPICE-fullscreen capture, Pico HID over WiFi)" with "verified live against the rig (originally VM target; physical Win10 laptop as of the 2026-07-20 move, appliance HID over UART)". Also update the pointer to HOLO_INTEGRATION_PLAN.md to note it is SUPERSEDED.
2. `kvm_agent/instrumentation/run_log.py:3`: the citation `PROJECT_GUIDANCE_holo.md §3.3` refers to a file that never existed in the repo. Re-cite as `AGENTS.md §1 (all artifacts in runs/) and the 2026-07-18 harness review` and drop the phantom filename. (Keep the quoted recording discipline — it's accurate.)
3. `kvm_agent/models/holo.py:133`: the comment "Backed by ENV.r4.key()/combo() (kvm_agent/hardware/pico_client.py)" — replace `pico_client.py` with `kvm_agent/hardware/appliance.py`.

- [ ] **Step 4: Create the session doc skeleton**

Create `docs/SESSION_2026-07-20_physical_target_move.md` with sections: `## What changed` (point at this plan's task list), `## Live shakedown results` (TBD — filled in Task 11), `## Settle-threshold revalidation` (TBD), `## Learned` (TBD). The TBDs are filled during Task 11, not left as placeholders past it.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: project state for the physical target; header/comment truth pass"
```

---

### Task 11: Live shakedown on the physical laptop (rig time — gates, no code)

**Files:**
- Uses: `tools/hid_smoke.py` (create below), `tools/battery.py`, `tools/battery_tasks_shakedown.json`
- Fills in: `docs/SESSION_2026-07-20_physical_target_move.md`

**Interfaces:**
- Consumes: everything. This is the acceptance test per spec §6/§7.
- Produces: the first honest baseline; calibration data for the settle threshold.

This task requires the physical rig. ALL offline gates from Tasks 1–10 must be green first (AGENTS.md §4).

- [ ] **Step 1: Create `tools/hid_smoke.py` (offline gate: py_compile only)**

```python
#!/usr/bin/env python3
"""
hid_smoke.py — first-contact test for a new physical target
(docs/PLAN_2026-07-20_physical_target_move.md §6 step 2).

  1. probes the appliance (BOTH HID collections must report online — the composite
     device can come up half-dead: I2, REPORT_2026-07-19_problems.md)
  2. types a known string via HID into whatever has focus (operator opens Notepad)
  3. saves a full-res evidence frame to runs/hid_smoke_<ts>/
  4. OCRs it with the tesseract CLI if installed and prints what it read

Every actuation/observation layer is exercised; any divergence localizes the fault.

    python tools/hid_smoke.py
"""
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.hardware.appliance import ApplianceClient
from kvm_agent.hardware.env import Camera

STRING = "holo smoke 123"


def main():
    run_dir = os.path.join(CFG.runs_dir, f"hid_smoke_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)

    r4 = ApplianceClient()
    probe = r4.probe()
    ack = str(probe.get("ack"))
    print("[smoke] probe:", ack)
    assert "kbd=1" in ack and "mouse=1" in ack, f"HALF-DEAD HID COLLECTION: {ack}"

    cam = Camera(CFG.cam_index, *CFG.screen_size)
    try:
        input(f"[smoke] open Notepad on the laptop, click inside it, press Enter — "
              f"will type {STRING!r}...")
        r4.type(STRING)
        time.sleep(1.0)
        png = cam.png_bytes(full_res=True)
    finally:
        cam.release()
    frame_path = os.path.join(run_dir, "evidence.png")
    with open(frame_path, "wb") as f:
        f.write(png)
    print(f"[smoke] evidence frame -> {frame_path}")

    tess = shutil.which("tesseract")
    if tess:
        out = subprocess.run([tess, frame_path, "stdout"],
                             capture_output=True, text=True).stdout
        print(f"[smoke] tesseract read: {out.strip()!r} (expected substring {STRING!r})")
    else:
        print("[smoke] tesseract not installed — verify the string on the frame by eye")


if __name__ == "__main__":
    main()
```

Run: `python -m py_compile tools/hid_smoke.py` — expected exit 0. Commit: `git add -A && git commit -m "tools: hid_smoke first-contact test for the physical target"`.

- [ ] **Step 2: Cable and boot**

Laptop lid closed, HDMI out → capture card → passthrough to the user's monitor. Boot to the Windows desktop. Gate: `python -c "from kvm_agent.config import CFG; from kvm_agent.hardware.env import Camera; c=Camera(CFG.cam_index,*CFG.screen_size); f=c.read(); print(f.shape); c.release()"` prints a real frame shape. Record the panel's native resolution in the session doc; if it isn't 1920×1080, set `SCREEN_W`/`SCREEN_H` env vars (and the bridge's `--screen-w/--screen-h` on the Pi) to match before proceeding.

- [ ] **Step 3: HID smoke test**

Run: `python tools/hid_smoke.py`
Gate: probe shows `kbd=1 mouse=1`; the evidence frame shows `holo smoke 123` in Notepad (OCR or eyeball). If the probe shows a half-dead collection: reboot the laptop once and retry; if persistent, STOP — that's a new Blame-Ledger row to investigate before any battery number means anything.

- [ ] **Step 4: One task end-to-end**

Run: `python tools/battery.py` with a one-task file containing only `notepad_type` (copy `tools/battery_tasks_shakedown.json`, trim to the first entry). Grade it yourself. Gate: the run dir in `runs/` contains step frames + raw outputs + summary; the grade lands in `runs/battery_<ts>_results.json`.

- [ ] **Step 5: Full shakedown battery**

Enable Steps Recorder (psr.exe) on the laptop (raise the 100-capture cap in its settings). Run: `python tools/battery.py tools/battery_tasks_shakedown.json`. Afterward, retrieve the psr .zip(s) into the battery's run dirs. Record the score in the session doc — this is the first honest baseline (REPORT_2026-07-19 §6 item 1).

- [ ] **Step 6: Settle-threshold revalidation**

From the battery's recorded step frames: compute `_tile_max_diff` over consecutive idle frames (e.g. from `runs/battery_*` steps where no action fired) and confirm the static noise floor is well below `thresh=3.0`. If the floor differs materially, adjust the `wait_until_stable` default and record the calibration in the session doc. Fill in the session doc's `## Live shakedown results`, `## Settle-threshold revalidation`, and `## Learned` sections.

- [ ] **Step 7: Clonezilla image + close-out**

Take a Clonezilla disk image of the clean Win10 state (the reset backstop). Update `PROJECT_STATE.md` §4 with the baseline score. Final: `python tests/test_frame_diff.py && python tests/test_frame_buffer.py && python tests/test_settle.py && python tests/test_target.py && python tests/test_battery.py && python tests/test_clear_hid.py` all `ALL PASS`; `git status` clean.

```bash
git add -A
git commit -m "docs: physical shakedown baseline + settle revalidation"
```

---

## Self-Review Notes (author-filled)

- **Spec coverage:** §2 topology → Tasks 7, 11. §3 retirements → Tasks 1, 2, 9 (HOLO stamp in 1; pycache in 1; pyproject/comments in 9/10). §4 trust fixes → Tasks 3, 4+5, 6. §5 battery → Task 8. §6 shakedown → Task 11 (+ hid_smoke). §7 testing → per-task steps + 11.7. §8 close-out → Tasks 10, 11.7. §9 deferred → PROJECT_STATE §4 + no tasks (deliberate).
- **Ordering:** Task 9 deletes vm_*/planner_* config fields — safe only after Task 1 archived their consumers. Task 5 depends on Task 4's `wait_newer`. Task 8 depends on Task 7. Everything else is order-independent within its file.
- **`git mv` caveat** handled via shell `mv` + `git add -A` (gitignored `waa/results` etc. travel with the move; repo rule: artifacts technically belong in `runs/` — they ride into `_archive` unchanged rather than being re-homed, since `_archive` is write-only and they're historical).
