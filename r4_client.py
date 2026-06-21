"""
r4_client.py — host-side controller for the UNO R4 HID listener (robustness pass).

Holds one persistent TCP connection and streams compact commands. New in this
version:
  #2 type() splits on newlines and sends an explicit Return between segments,
     so "type ending in \n" reliably means "type then Enter" — and a raw '\n'
     is never embedded in a command (which the sketch's line reader would eat).
  #3 combo() parses "ctrl+s" / "ctrl+shift+t" / "alt+Tab" into keycodes and
     fires them as a held combo.
  #4 scroll() sends wheel ticks.
"""

import socket
import time

# Set to the R4's (reserved) IP.
R4_IP   = "192.168.0.183"
R4_PORT = 8000

# EvoCUA / xdotool key names -> names the Pico firmware (code.py) accepts.
# The Pico lowercases tokens and matches its _NAMED table or a single char,
# so we only need to remap the names whose spelling differs (underscores,
# X11 _L/_R suffixes, "super"/"meta" which the Pico calls "gui").
NAME_ALIASES = {
    "return": "enter",
    "page_up": "pageup", "pgup": "pageup",
    "page_down": "pagedown", "pgdn": "pagedown",
    "super": "gui", "super_l": "gui", "super_r": "gui",
    "meta": "gui", "meta_l": "gui", "meta_r": "gui",
    "command": "gui", "cmd": "gui",
    "control": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "option": "alt",
    "del": "delete",
}


def norm_key(token: str) -> str:
    """Map a model/xdotool key name to the Pico firmware's vocabulary."""
    t = token.strip().lower()
    return NAME_ALIASES.get(t, t)


class R4:
    def __init__(self, ip=R4_IP, port=R4_PORT):
        self.ip, self.port = ip, port
        self._connect()

    def _connect(self):
        self.sock = socket.create_connection((self.ip, self.port), timeout=5)
        # The firmware sends NO reply to commands, so every _send's recv() just
        # waits out this timeout before returning "". At 2s that was ~2s of dead
        # time PER command — fine for clicks (masked by the 5s settle) but it made
        # typing ~2s/char and overflowed long command bursts. 0.25s keeps a small
        # pacing yield while letting commands return promptly. A real connection
        # drop still raises ConnectionResetError (not a timeout) -> _send reconnects.
        self.sock.settimeout(0.25)

    def _send(self, cmd: str) -> str:
        # Auto-reconnect on a dropped connection. The Pico firmware uses a blocking
        # recv with no idle timeout, so it never closes an idle connection — but a
        # transient WiFi blip on the Pico forcibly resets it (WinError 10054), and
        # the firmware immediately loops back to accept(). Without this, ONE drop
        # mid-run silently fails every remaining action (observed 2026-06-19: the
        # connection died after reset+WAIT and all 8 clicks no-op'd). Reconnect once
        # and resend so a single blip doesn't kill the whole rollout.
        data = (cmd + "\n").encode()
        # Retry across a brief Pico recycle. The firmware now uses a FINITE per-connection
        # timeout (CONN_TIMEOUT in code.py), so a stuck/half-open connection is torn down and
        # the Pico returns to accept() instead of wedging forever. A genuine reset (RST) makes
        # sendall raise OSError -> close, reconnect and resend; a few tries with backoff span
        # the reconnect so a transient blip doesn't fail the whole rollout.
        TRIES = 4
        for attempt in range(1, TRIES + 1):
            try:
                self.sock.sendall(data)
                try:
                    return self.sock.recv(64).decode().strip()
                except socket.timeout:
                    return ""
            except OSError:
                if attempt == TRIES:
                    raise
                try:
                    self.sock.close()
                except Exception:
                    pass
                time.sleep(0.3 * attempt)   # 0.3 / 0.6 / 0.9s backoff
                self._connect()
        return ""

    # mouse
    def move(self, x, y):   return self._send(f"M{x},{y}")
    def click(self):        return self._send("C")
    def rclick(self):       return self._send("R")
    def down(self):         return self._send("D")
    def up(self):           return self._send("U")
    def home(self):         return self._send("H")
    def scroll(self, ticks): return self._send(f"S{int(ticks)}")   # +up / -down

    # keyboard — the Pico firmware (code.py) expects NAMES, not numeric codes:
    #   K<name>  -> tap(name)     X<a>+<b>+<c> -> combo(names)     T<text> -> type
    def key(self, name):
        # The agent's type-rewrite emits text as individual press(<char>) calls.
        # A Keycode tap can name letters and special keys, but NOT digits, most
        # punctuation, or space, and our host lowercasing dropped capitalization —
        # so "ZIP 45209 weather" came out "zipweather": the space cut the string,
        # the digits vanished, and caps were lost (observed 2026-06-19). Fix:
        #   - a single PRINTABLE char -> TYPE path (the Pico's US keyboard layout
        #     produces the exact glyph: digits, punctuation, shifted capitals);
        #   - a literal space -> the firmware's NAMED "space" key (Keycode.SPACE),
        #     since the type path's arg gets stripped of lone spaces;
        #   - multi-char tokens (enter/esc/tab/arrows/…) -> Keycode tap as before.
        s = str(name)
        if s == " ":
            return self._send("Kspace")
        if len(s) == 1:
            return self._send("T" + s)
        return self._send("K" + norm_key(s))

    def type(self, text):
        # split on newlines: type each segment, press Enter between segments.
        # never embeds a raw '\n' in a command (the firmware delimits on '\n').
        parts = text.split("\n")
        out = ""
        for i, seg in enumerate(parts):
            if seg:
                out = self._send("T" + seg)
            if i < len(parts) - 1:
                out = self._send("Kenter")
        return out

    def combo(self, text):
        # "ctrl+s", "ctrl+shift+t", "alt+Tab" -> normalized names, held together.
        parts = [norm_key(p) for p in text.split("+") if p.strip()]
        if not parts:
            return f"skip-combo:{text}"
        return self._send("X" + "+".join(parts))

    # convenience
    def click_at(self, x, y):
        self.move(x, y)
        return self.click()

    def drag(self, x1, y1, x2, y2):
        self.move(x1, y1); self.down()
        self.move(x2, y2); self.up()

    def close(self):
        self.sock.close()


if __name__ == "__main__":
    r4 = R4()
    print("connected")
    r4.home()
    input("cursor at top-left? Enter...")
    r4.move(960, 540); input("center? Enter...")
    print("combo test: focus a text field, then Enter to send Ctrl+A")
    input()
    r4.combo("ctrl+a")
    r4.close()
