"""
appliance.py -- host-side client for the Pi 5 HID appliance (Stage 6).

Drop-in replacement for the WiFi `R4` Pico client (kvm_agent.hardware.pico_client):
same method surface (move/click/rclick/down/up/home/scroll/key/combo/type/
click_at/drag/close), but each call hits the Pi 5 `hid_bridge` HTTP API instead
of a fire-and-forget WiFi socket. The bridge returns the Pico's real per-command
ACK, so a dropped/failed command raises ApplianceError LOUDLY here rather than
silently succeeding -- the core fix from docs/FINDINGS_2026-07-18_harness_review.md
(#1 no-ack, #2 reconnect-masks-dead-HID).

Capture is unchanged (still the host `Camera`); only the action channel moves to
the appliance. So PicoEnv keeps its `cam` and swaps only `r4`.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from kvm_agent.config import CFG


class ApplianceError(RuntimeError):
    pass


class _CommandLog:
    """Host-side half of the 2026-07-19 command-logging fix (see hid_bridge.py's
    CommandLogger for the appliance side -- same JSONL-per-line, flush-per-write design).
    Before this, NOTHING was logged anywhere: not what the host sent, not what came back.
    Now every _req() call is one line here, and since the Pi 5's response already includes
    the decoded Pico wire-level data (kbd_online/mouse_online/LEDs/wire_ms -- see
    hid_bridge.py's _wire_info), this single log captures the WHOLE chain: what was sent,
    what the appliance did, and what the target (Windows guest) actually reported back,
    with no separate correlation step needed."""

    _DEFAULT_PATH = os.path.join(CFG.logs_dir, "appliance_client_commands.jsonl")

    def __init__(self, path=None):
        self.path = path or self._DEFAULT_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.lock = threading.Lock()

    def write(self, record):
        record["ts"] = time.time()
        line = json.dumps(record)
        with self.lock:
            with open(self.path, "a") as f:
                f.write(line + "\n")
                f.flush()


CMD_LOG = _CommandLog()


class ApplianceClient:
    def __init__(self, base_url=None, timeout=5.0):
        self.base = (base_url or CFG.appliance_url).rstrip("/")
        self.timeout = timeout

    def _req(self, path, method="POST", **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.load(r)
        except Exception as e:
            CMD_LOG.write({"path": path, "params": params, "ok": False,
                           "error": f"transport error: {e}", "http_ms": round((time.time() - t0) * 1000.0, 1)})
            raise ApplianceError(f"{path} transport error: {e}")
        CMD_LOG.write({"path": path, "params": params, "ok": data.get("ok"),
                       "ack": data.get("ack"), "wire": data.get("wire"),
                       "http_ms": round((time.time() - t0) * 1000.0, 1)})
        if not data.get("ok"):
            raise ApplianceError(f"{path} not ok: {data.get('ack') or data.get('error') or data}")
        return data

    # --- mouse (R4-compatible surface) ---
    def move(self, x, y):    return self._req("/hid/move", x=int(x), y=int(y))
    def click(self):         return self._req("/hid/click")
    def rclick(self):        return self._req("/hid/rclick")
    def down(self):          return self._req("/hid/down")
    def up(self):            return self._req("/hid/up")
    def home(self):          return self._req("/hid/home")
    def scroll(self, ticks): return self._req("/hid/scroll", ticks=int(ticks))

    # --- keyboard ---
    def key(self, name):     return self._req("/hid/key", name=str(name))
    def combo(self, spec):   return self._req("/hid/combo", spec=str(spec))

    def type(self, text):
        # The UART protocol is newline-framed, so a literal '\n' can't ride inside a
        # single T command. Split on newlines: type each segment, press Enter between --
        # exactly what R4.type did host-side, so "type ending in \n" == "type then Enter".
        parts = str(text).split("\n")
        last = None
        for i, seg in enumerate(parts):
            if seg:
                last = self._req("/hid/type", text=seg)
            if i < len(parts) - 1:
                last = self._req("/hid/key", name="enter")
        return last

    # --- convenience (match R4) ---
    def click_at(self, x, y):
        self.move(x, y)
        return self.click()

    def drag(self, x1, y1, x2, y2):
        # NOTE: teleport drag (no intermediate waypoints), same as R4.drag -- some apps
        # need a slower drag; revisit if a task needs it. Not made worse than before.
        self.move(x1, y1); self.down()
        self.move(x2, y2); self.up()

    # --- appliance-specific ---
    def probe(self):
        return self._req("/hid/probe")

    def health(self):
        return self._req("/health", method="GET")

    def set_screen(self, w, h):
        """Tell the bridge the ACTUAL capture resolution (2026-07-19), so its pixel->wire-
        range scale factor matches reality instead of a --screen-w/--screen-h launch default
        that has to independently agree with kvm_agent.config.CFG.screen_w/h by convention.
        See kvm_agent.hardware.env.PicoEnv.__init__ for the caller (right after Camera opens
        and the real negotiated frame size is known) and hid_bridge.py's _cmd_set_screen for
        why this doesn't need a Pico firmware reflash."""
        return self._req("/hid/set_screen", w=int(w), h=int(h))

    def close(self):
        pass  # stateless HTTP; nothing to release


# name parity with pico_client.R4 / PicoClient for readability at call sites
PicoAppliance = ApplianceClient
