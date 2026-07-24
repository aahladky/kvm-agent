"""
appliance.py -- host-side client for the Pi 5 HID appliance (Stage 6).

Drop-in replacement for the WiFi `R4` Pico client (kvm_agent.hardware.pico_client):
same method surface (move/click/rclick/down/up/home/scroll/key/combo/type/
click_at/drag/close), but each call hits the Pi 5 `hid_bridge` HTTP API instead
of a fire-and-forget WiFi socket. The bridge returns the Pico's real per-command
ACK, so a dropped/failed command raises ApplianceError LOUDLY here rather than
silently succeeding -- the core fix from _archive/docs_history/FINDINGS_2026-07-18_harness_review.md
(#1 no-ack, #2 reconnect-masks-dead-HID).

Capture is unchanged (still the host `Camera`); only the action channel moves to
the appliance. So PicoEnv keeps its `cam` and swaps only `r4`.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from kvm_agent.config import CFG


class ApplianceError(RuntimeError):
    pass


class ApplianceClient:
    def __init__(self, base_url=None, timeout=5.0):
        self.base = (base_url or CFG.appliance_url).rstrip("/")
        self.timeout = timeout
        self._events = []
        self._events_lock = threading.Lock()

    def _record_event(self, event):
        with self._events_lock:
            self._events.append(event)

    def drain_events(self):
        """Return and clear exact HTTP/Pico responses since the previous drain.

        The loop drains this once per normalized action and stores the records in that
        step's run artifact. The appliance daemon's persistent log remains useful for
        crash diagnosis, but is no longer the sole copy of delivered-wire evidence.
        """
        with self._events_lock:
            events, self._events = self._events, []
        return events

    def _req(self, path, method="POST", _timeout=None, **params):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=_timeout or self.timeout) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            # The bridge answers failures with 502/404/400 + a JSON body carrying its
            # own ack/error detail (e.g. "pico error code=0x45"). Surface that detail
            # (2026-07-21 review P1-11): previously it was swallowed into a bare
            # "transport error" and the carefully constructed bridge error never
            # reached the caller.
            detail = ""
            try:
                body = json.loads(e.read())
                detail = body.get("ack") or body.get("error") or ""
            except Exception:
                pass
            message = f"{path} transport error: {e}" + (f" ({detail})" if detail else "")
            self._record_event({"path": path, "method": method, "params": params,
                                "ok": False, "error": message})
            raise ApplianceError(message)
        except Exception as e:
            message = f"{path} transport error: {e}"
            self._record_event({"path": path, "method": method, "params": params,
                                "ok": False, "error": message})
            raise ApplianceError(message)
        if not data.get("ok"):
            message = f"{path} not ok: {data.get('ack') or data.get('error') or data}"
            self._record_event({"path": path, "method": method, "params": params,
                                "ok": False, "response": data, "error": message})
            raise ApplianceError(message)
        self._record_event({"path": path, "method": method, "params": params,
                            "ok": True, "response": data})
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
                # The bridge types at ~60-90ms/char (pikvm_proto pacing), so the default
                # 5s timeout false-fires on ~65+ char segments -- a "transport error"
                # while the Pi types to completion: false failure AND target-side
                # divergence (2026-07-21 second review #5). Scale the budget to the text.
                last = self._req("/hid/type", text=seg,
                                 _timeout=max(self.timeout, 5.0 + 0.12 * len(seg)))
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
    def clear_hid(self):
        """All-keys-up: release every held key/button on the target. Called on connect
        and on close() so a mid-fault latched modifier (combo interrupted by a link
        failure) can't corrupt the next session's input state."""
        return self._req("/hid/clear")

    def probe(self):
        return self._req("/hid/probe")

    def set_screen(self, w, h):
        """Sync the bridge's pixel->wire-range scale factor to the target's REAL display
        resolution (the deployed bridge has supported /hid/set_screen since 2026-07-19;
        this client method was missing on this line until now). Must be called whenever
        the target's display resolution changes -- e.g. the 2026-07-21 native-720p A/B:
        with the laptop rendering at 1280x720 but the bridge still scaling for
        1920x1080, every click lands stretched away from its target."""
        return self._req("/hid/set_screen", w=int(w), h=int(h))

    def health(self):
        return self._req("/health", method="GET")

    def close(self):
        pass  # stateless HTTP; nothing to release


# name parity with pico_client.R4 / PicoClient for readability at call sites
PicoAppliance = ApplianceClient
