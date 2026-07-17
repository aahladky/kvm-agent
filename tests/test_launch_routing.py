"""
test_launch_routing.py — OFFLINE tests for the 2026-06-21 launch fix.

No rig: env/agent/verifier are fakes and the HID calls are recorded. Covers Win+R-vs-Start-menu
routing and the 'Windows cannot find <x>' guard (the live Firefox blocker that looped the replanner).

    python tests\test_launch_routing.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kvm_agent.orchestration.executive as exe
from kvm_agent.orchestration.executive import Executive

exe.time.sleep = lambda *a, **k: None     # don't actually wait during launch() in tests

_FAILS = []
def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        _FAILS.append(name)


class FakeR4:
    def __init__(self): self.calls = []
    def combo(self, c): self.calls.append(("combo", c))
    def key(self, k): self.calls.append(("key", k))
    def type(self, t): self.calls.append(("type", t))
    def move(self, *a): self.calls.append(("move", a))
    def click(self): self.calls.append(("click", None))


class FakeEnv:
    def __init__(self): self.r4 = FakeR4()
    def observe(self): return {"screenshot": b"PNG"}


class FakeVerifier:
    def __init__(self, text=""): self._t = text
    def read_text(self, png): return self._t


class FakeAgent:
    pass


def make_ex(text=""):
    return Executive(FakeEnv(), FakeAgent(), verifier=FakeVerifier(text), capture=False)


# 1. _is_winr_target: system commands / .exe / URIs -> Win+R; app names -> Start ---------------
ex = make_ex()
for t in ("cmd", "notepad", "calc", "explorer", "control", "ms-settings:defaultapps",
          "foo.exe", r"C:\Windows\system32\notepad.exe"):
    check(f"winr target: {t!r}", ex._is_winr_target(t) is True)
for t in ("Firefox", "chrome", "Mozilla.Firefox", "Google Chrome", "Spotify", "vlc"):
    check(f"app (not winr): {t!r}", ex._is_winr_target(t) is False)

# 2. _launch_error_dialog: detects the 'cannot find' dialog by its text -----------------------
check("error dialog: 'cannot find' -> True",
      make_ex("x Firefox Windows cannot find 'Firefox'. Make sure you typed")._launch_error_dialog() is True)
check("error dialog: normal screen -> False",
      make_ex("Mozilla Firefox  New Tab  History  Bookmarks")._launch_error_dialog() is False)
check("error dialog: empty -> False", make_ex("")._launch_error_dialog() is False)

# 3. routing: an app name opens Start (Win); a system command uses Win+R -----------------------
def routed_calls(app):
    e = make_ex("")
    e._changed = lambda b, a, thresh=6.0: True     # pretend a window appeared (fast path)
    e._app_open = lambda *a, **k: True
    e._launch_error_dialog = lambda *a, **k: False
    ok = e.launch(app, s=0)
    return ok, e.env.r4.calls

ok, calls = routed_calls("Firefox")
check("launch Firefox returns True", ok is True)
check("launch Firefox opens Start (key 'win')", ("key", "win") in calls)
check("launch Firefox does NOT use Win+R", ("combo", "win+r") not in calls)

ok, calls = routed_calls("cmd")
check("launch cmd returns True", ok is True)
check("launch cmd uses Win+R", ("combo", "win+r") in calls)
check("launch cmd does NOT open Start", ("key", "win") not in calls)

# 4. cannot-find guard: never success; dialog dismissed with Esc -------------------------------
ex = make_ex("")
ex._changed = lambda *a, **k: False
ex._app_open = lambda *a, **k: False
ex._launch_error_dialog = lambda *a, **k: True       # every attempt shows the error
check("cannot-find: launch returns False", ex.launch("Firefox", s=0, retries=1) is False)
check("cannot-find: dialog dismissed with Esc", ("key", "esc") in ex.env.r4.calls)

# 5. fallback: error on attempt 1, opens on attempt 2 -> True, two Start tries -----------------
ex = make_ex("")
seen = {"n": 0}
def err_once(*a, **k):
    seen["n"] += 1
    return seen["n"] == 1            # error only on the first confirm
ex._launch_error_dialog = err_once
ex._changed = lambda *a, **k: True   # 2nd attempt "opens"
ex._app_open = lambda *a, **k: False
check("fallback: eventually returns True", ex.launch("Firefox", s=0, retries=2) is True)
check("fallback: Esc'd the first error", ("key", "esc") in ex.env.r4.calls)
check("fallback: retried Start search (>=2 Win taps)",
      sum(1 for c in ex.env.r4.calls if c == ("key", "win")) >= 2)

# 6. _is_exe_path: a full path to an .exe -> cmd route; bare names / URIs are NOT exe paths -------
ex = make_ex()
for t in (r"C:\Program Files\Mozilla Firefox\firefox.exe",
          r'"C:\Program Files\Mozilla Firefox\firefox.exe"', "/opt/app/run.exe"):
    check(f"exe path: {t!r}", ex._is_exe_path(t) is True)
for t in ("Firefox", "cmd", "ms-settings:defaultapps", "Mozilla.Firefox", "foo.exe"):
    check(f"not an exe path: {t!r}", ex._is_exe_path(t) is False)

# 7. _exe_friendly_name: basename without .exe, mapped to a display name when known ---------------
check("friendly name: firefox.exe -> Firefox",
      ex._exe_friendly_name(r"C:\Program Files\Mozilla Firefox\firefox.exe") == "Firefox")
check("friendly name: calc.exe -> Calculator (display map)",
      ex._exe_friendly_name(r"C:\Windows\system32\calc.exe") == "Calculator")
check("friendly name: handles surrounding quotes",
      ex._exe_friendly_name(r'"C:\x\myapp.exe"') == "Myapp")

# 8. launch(full exe path) -> opens cmd and runs `start`, confirms via vision (NOT Win+R typing) --
e = make_ex("")
e._app_open = lambda name=None, *a, **k: True          # cmd opens; the app confirms open
ok = e.launch(r"C:\Program Files\Mozilla Firefox\firefox.exe", s=0)
check("exe launch: returns True", ok is True)
check("exe launch: opened cmd (typed 'cmd')", ("type", "cmd") in e.env.r4.calls)
check("exe launch: ran start with the quoted path + exit",
      ("type", 'start "" "C:\\Program Files\\Mozilla Firefox\\firefox.exe" & exit') in e.env.r4.calls)

# 9. MISFIRE: a launch that falls into Start search ('No results') must FAIL, not false-confirm ---
e = make_ex("No results found for 'Firefox'")          # OCR sees the Start-search panel
e._changed = lambda *a, **k: True                      # a panel APPEARED (frame-diff would accept)
e._app_open = lambda *a, **k: False
e._launch_error_dialog = lambda *a, **k: False
check("misfire: search panel is NOT accepted as launched", e.launch("Firefox", s=0, retries=0) is False)
check("misfire: the panel was Esc'd", ("key", "esc") in e.env.r4.calls)

# 10. _launch_misfired unit: search/Bing signals vs a real app window ------------------------------
check("misfired: 'No results found' -> True",
      make_ex("No results found for 'firefox'")._launch_misfired(b"x", "Firefox") is True)
check("misfired: a Bing search echoing the name -> True",
      make_ex("bing search results firefox download")._launch_misfired(b"x", "Firefox") is True)
check("misfired: a real Firefox window -> False",
      make_ex("Mozilla Firefox  New Tab  History")._launch_misfired(b"x", "Firefox") is False)
check("misfired: benchmark Notepad window -> False",
      make_ex("Untitled - Notepad")._launch_misfired(b"x", "notepad") is False)

print("\n" + ("ALL PASS" if not _FAILS else f"{len(_FAILS)} FAILED: {_FAILS}"))
sys.exit(1 if _FAILS else 0)
