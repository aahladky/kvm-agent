"""
executive.py — hierarchical executive/executor for the KVM-over-IP rig.

THE ARCHITECTURE (this is the fix for reliable multi-step UI navigation):

  PLANNER  (pluggable: Claude now, a local model on the B580 later — see planner.py)
      decides WHAT to do: decomposes a natural-language goal into a structured plan
      of atomic STEPS, and re-plans when the executive reports a step failed.

  EXECUTIVE  (this module)
      runs each step with the RIGHT primitive instead of forcing everything through
      one fragile channel:
        - KEYBOARD-FIRST for launch / type / hotkey  -> deterministic, no grounding
          (Win+R app-launch kills the taskbar-grounding flail; typed arithmetic kills
           the dense-keypad misgrounding the prior work spent days on).
        - UI-TARS STATELESS grounding ONLY for genuinely visual targets (a button with
          no keyboard path). reset() every call => no history => no coordinate-mimicry.

  VERIFIER
      confirms each step's effect and the final goal from the SCREEN (OCR / vision /
      frame-diff) — never from the model's self-report. This closes the prior
      false-positive-terminate failure mode.

Why this beats the prior single-7B loop: the model no longer has to plan + track state
+ ground + decide termination simultaneously; the plan IS the (lossless) state; fragile
clicks become keystrokes; success is verified, not asserted.

Usable two ways:
  - inject an already-open rig:  Executive(env, agent, verifier)
        (env = pico_env.PicoEnv, agent = a UITARSAgent; lets the live REPL reuse its
         single open camera+Pico)
  - stand alone:                 ex = Executive.open(); ... ; ex.close()
"""
import os, re, io, time, json, base64, hashlib, urllib.request

from kvm_agent.config import CFG
os.environ.setdefault("OPENAI_BASE_URL", CFG.openai_base)
os.environ.setdefault("OPENAI_API_KEY", CFG.openai_key)

_OLLAMA = CFG.ollama_base


# ──────────────────────────────────────────────────────────── verification
class Verifier:
    """Read text / answer yes-no questions about a frame.

    Order of preference: pytesseract (deterministic, fast, no GPU) -> a vision model
    on the laptop (qwen2.5vl via Ollama; general but causes an Ollama model swap) ->
    None (unknown; the executive treats None as 'cannot verify', not 'failed')."""

    def __init__(self, vision_model="qwen2.5vl:7b"):
        self.vision_model = vision_model
        self._tess = None
        try:
            import os, shutil
            import pytesseract  # noqa
            from PIL import Image  # noqa
            # auto-discover tesseract.exe so a `winget`/UB-Mannheim install works WITHOUT
            # PATH fiddling: explicit TESSERACT_CMD (config) -> PATH -> standard Windows dirs.
            cmd = CFG.tesseract_cmd or shutil.which("tesseract")
            if not cmd:
                for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                          r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
                    if os.path.exists(p):
                        cmd = p
                        break
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            # actually exercise it so a missing tesseract.exe is caught now, not later
            pytesseract.get_tesseract_version()
            self._tess = pytesseract
        except Exception:
            self._tess = None

    def read_text(self, png: bytes) -> str:
        if self._tess is not None:
            try:
                from PIL import Image
                return self._tess.image_to_string(Image.open(io.BytesIO(png)))
            except Exception:
                pass
        # vision fallback
        out = self._vision(png, "Transcribe all text visible on this screen. Output only the text.")
        return out or ""

    def has_text(self, png: bytes, expect: str) -> "bool|None":
        """Substring-match `expect` against the screen's transcribed text. Works for BOTH
        backends: tesseract OCR when present, else the vision model TRANSCRIBES and we match
        host-side. (The old vision path asked a strict yes/no about the literal string, which
        failed on truncated/partial expects like 'hello from the packag' even though
        'package' was on screen.) None only if nothing could read the screen."""
        txt = self.read_text(png)
        if not txt:
            return None
        return expect.lower() in txt.lower()

    def read_number(self, png: bytes) -> "str|None":
        """The number on the calculator DISPLAY — read by the VISION model, which localizes
        to the display semantically. Whole-screen tesseract can't separate the result from
        the taskbar clock/date and other clutter (by length it grabbed '2026.'; by glyph
        height it grabbed tiny stray digits), so for this one we ask the model directly.
        (Text-presence verify still uses the fast tesseract path.)"""
        ans = self._vision(
            png, "What number is shown on the calculator display? Reply with ONLY the number.")
        if not ans:
            return None
        m = re.search(r"-?\d[\d,]*\.?\d*", ans)
        return m.group(0).replace(",", "") if m else None

    def _vision(self, png: bytes, prompt: str) -> "str|None":
        try:
            body = json.dumps({
                "model": self.vision_model, "prompt": prompt,
                "images": [base64.b64encode(png).decode()],
                "stream": False, "options": {"temperature": 0},
            }).encode()
            req = urllib.request.Request(_OLLAMA + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=60)).get("response", "").strip()
        except Exception:
            return None

    def available(self) -> dict:
        """Which grading backends actually work right now. The battery uses this to refuse to
        silently score on the model's self-report when both are down (flaw #8): a grader that
        returns None because its backend is unreachable must not read as 'verified correct'."""
        tess = self._tess is not None
        vision = False
        try:
            urllib.request.urlopen(_OLLAMA + "/api/tags", timeout=3)
            vision = True
        except Exception:
            vision = False
        return {"tesseract": tess, "vision": vision, "any": bool(tess or vision)}


# ──────────────────────────────────────────────────────────── executive
class Executive:
    def __init__(self, env, agent, verifier=None, log_dir=None, settle=1.0, capture=True,
                 guard_dialogs=True):
        self.env = env
        self.agent = agent           # UITARSAgent (the stateless executor)
        # the executive uses the agent ONLY as a grounder -> force UI-TARS into click-only
        # GROUNDING mode so it can't emit finished()/scroll on a visible target (isolated
        # 2026-06-21: COMPUTER_USE prompt DONE'd instead of clicking a visible chooser item).
        # No-op for agents without the flag (e.g. EvoCUA).
        try:
            self.agent.grounding = True
        except Exception:
            pass
        self.verifier = verifier or Verifier()
        self.settle = settle
        self.log_dir = log_dir
        self.log = []
        # observability: capture a per-step frame + the grounder's raw output every run, so a
        # failure is diagnosable from the log instead of needing a bespoke harness rerun (the
        # gap that made this session's bugs invisible). Off for the timing-sensitive benchmark.
        self.capture = capture
        # closed-loop: auto-dismiss a blocking ERROR dialog before a click (the benchmark has no
        # click ops, so this is a no-op there). A harness can pass guard_dialogs=False for zero cost.
        self.guard_dialogs = guard_dialogs
        # hard recalled constraints the executive ENFORCES (set by the orchestrator from memory):
        # a step whose op+salient-text matches a gate is BLOCKED before it runs, so a recalled
        # prohibition ("the FF shortcut is broken — don't launch it") actually changes behavior
        # instead of being soft-ignored. Empty by default -> a pure no-op (benchmark unaffected).
        self.hard_constraints = []
        self.last_ground = None      # raw grounding attempts of the most recent ground() call
        self.runs_root = log_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "runs")

    # -- construction for standalone use (opens the camera + Pico itself) --
    @classmethod
    def open(cls, executor_model=None, **kw):
        from kvm_agent.hardware.env import PicoEnv
        from kvm_agent.models.factory import make_agent
        env = PicoEnv(cam_index=CFG.cam_index, screen_size=CFG.screen_size, show=False)
        agent = make_agent("uitars", model=executor_model or CFG.executor_model, history=1,
                           temperature=0.0, screen_size=CFG.screen_size)
        return cls(env, agent, **kw)

    def close(self):
        try:
            self.env.close()
        except Exception:
            pass

    # ---- observation ----
    def observe(self) -> bytes:
        return self.env.observe()["screenshot"]

    @staticmethod
    def _sha(png):
        return hashlib.sha256(png).hexdigest()[:12] if png else None

    @staticmethod
    def _frame_diff(before: bytes, after: bytes) -> float:
        """Mean absolute pixel difference (0-255) over a downscaled grayscale.
        Live camera frames are NEVER byte-identical (sensor noise, the taskbar clock),
        so exact-hash equality is useless here — a window opening/closing moves a large
        fraction of pixels (diff in the tens) while capture noise stays ~<2."""
        import cv2, numpy as np
        try:
            a = cv2.imdecode(np.frombuffer(before, np.uint8), cv2.IMREAD_GRAYSCALE)
            b = cv2.imdecode(np.frombuffer(after, np.uint8), cv2.IMREAD_GRAYSCALE)
            a = cv2.resize(a, (160, 90)); b = cv2.resize(b, (160, 90))
            return float(np.mean(np.abs(a.astype("int16") - b.astype("int16"))))
        except Exception:
            return 999.0  # if we can't compare, assume changed (fail-open)

    def _changed(self, before: bytes, after: bytes, thresh: float = 3.5) -> bool:
        return self._frame_diff(before, after) > thresh

    # ---- deterministic keyboard primitives ----
    def key_combo(self, combo, s=None):
        self.env.r4.combo(combo); time.sleep(s if s is not None else self.settle)

    def tap(self, key, s=None):
        self.env.r4.key(key); time.sleep(s if s is not None else self.settle)

    def type_text(self, text, s=None):
        self.env.r4.type(text); time.sleep(s if s is not None else self.settle)

    def scroll(self, direction="down", amount=3, s=None):
        """Wheel-scroll to bring an off-screen target into view. Parks the cursor at screen center
        first so the wheel applies to the scrollable pane under it, then sends `amount` notches via
        the firmware wheel (r4.scroll: +up / -down). A GENERAL reach primitive — any task with
        below-the-fold targets (settings rows, long lists, web pages) needs it; the planner emits a
        scroll op then (re)grounds the now-visible target."""
        amt = abs(int(amount))
        ticks = amt if str(direction).strip().lower().startswith("u") else -amt
        self.env.r4.move(CFG.screen_w // 2, CFG.screen_h // 2)
        self.env.r4.scroll(ticks)
        time.sleep(s if s is not None else self.settle)

    # display names for the vision launch-confirm: Win+R takes the exe name, but the
    # window the verifier sees shows the friendly name (calc -> "Calculator").
    _APP_DISPLAY = {"calc": "Calculator", "notepad": "Notepad", "mspaint": "Paint",
                    "explorer": "File Explorer", "cmd": "Command Prompt",
                    "write": "WordPad", "wordpad": "WordPad"}

    # Win+R launches these reliably (system commands / consoles / control-panel tools). An
    # installed GUI app name (Firefox, Chrome, "Google Chrome"…) is NOT bare-name runnable via
    # Win+R — it must be opened via Start-menu search (see _is_winr_target / launch).
    _WINR_COMMANDS = {"cmd", "powershell", "pwsh", "explorer", "notepad", "calc", "mspaint",
                      "write", "wordpad", "control", "regedit", "taskmgr", "winver", "msconfig",
                      "cleanmgr", "dxdiag", "resmon", "perfmon", "msinfo32", "charmap", "osk",
                      "snippingtool", "wt"}

    def _app_open(self, app, png=None):
        """Vision check: is the just-launched app actually open on screen? Used as the
        launch-confirm FALLBACK when the frame-diff is too small to be sure — a compact
        window (e.g. Calculator) opening over an already-bright window (a maximized
        Notepad) scores only ~4-5 mean-diff at 160x90, well under the frame gate."""
        png = png if png is not None else self.observe()
        name = self._APP_DISPLAY.get(app.lower(), app)
        ans = self.verifier._vision(
            png, f"Is a {name} application window open and visible on this Windows "
                 f"screen? Answer with one word: 'yes' or 'no'.")
        return (ans is not None) and ("yes" in ans.lower())

    def _is_winr_target(self, app):
        """True if `app` is a Win+R-launchable system command / .exe / URI; False for an INSTALLED
        GUI app name (Firefox, Chrome…), which Win+R cannot run by bare name (it pops 'Windows
        cannot find <x>') — those route through Start-menu search instead."""
        a = str(app).strip().lower()
        if ":" in a:                       # ms-settings:… , shell:… -> Win+R runs URIs
            return True
        if a.endswith(".exe") or "\\" in a or "/" in a:   # an explicit exe / path
            return True
        return a in self._WINR_COMMANDS

    def _launch_error_dialog(self, png=None):
        """True if a 'Windows cannot find <x>' launch-error dialog is on screen. That dialog's
        title bar contains the app NAME, so the _app_open vision check FALSE-confirmed it as 'the
        app is open' on the live Firefox run. Detect it by its message text (tesseract, fast) and
        treat it as a launch FAILURE, never success."""
        txt = (self.verifier.read_text(png if png is not None else self.observe()) or "").lower()
        return any(p in txt for p in ("cannot find", "can't find", "couldn't find",
                                      "could not find"))

    # error/blocking-dialog phrases — a CONSERVATIVE set that is almost always an UNEXPECTED blocker
    # safe to Esc. Deliberately excludes legitimate flow dialogs (a first-run window, a Windows
    # "before you switch" default-app nag) so the guard never dismisses something we want to act on.
    _ERR_PHRASES = ("cannot find", "can't find", "couldn't find", "could not find",
                    "problem with shortcut", "this shortcut", "no longer work",
                    "has stopped working", "not responding", "unexpected error")

    def _blocking_dialog(self, png=None):
        """True if a blocking ERROR/problem dialog is on screen (by its text). Closed-loop pre-click
        guard: a stale error dialog from a prior step (e.g. the 'Problem with Shortcut' popup a broken
        launch leaves up) gets dismissed before we try to click, instead of grounding the click
        onto/under it. Conservative phrases so legitimate flow dialogs are NOT dismissed."""
        txt = (self.verifier.read_text(png if png is not None else self.observe()) or "").lower()
        return any(p in txt for p in self._ERR_PHRASES)

    # ---- hard-constraint gate (retrieval -> ENFORCEMENT; the "gap is code" fix) ----
    def set_constraints(self, constraints):
        """Arm the machine-enforceable gates derived from recalled memory (orchestrator-side).
        Each gate is {"op": <op|None>, "match": <substring>, "reason": <text>}: a step whose op
        matches (or op=None = any) AND whose salient text contains <match> is blocked. Cleared
        between tasks by the orchestrator so a constraint never leaks across goals."""
        self.hard_constraints = list(constraints or [])

    @staticmethod
    def _step_salient(step):
        """The action-bearing text of a step (what a constraint matches against)."""
        return " ".join(str(step.get(k, "")) for k in
                        ("app", "target", "text", "combo", "key", "ask", "expect")).strip()

    def _blocked_by_constraint(self, step):
        """Return the reason string if `step` violates an armed hard constraint, else None. The
        direct fix for retrieval != utilization: a recalled prohibition is enforced in CODE here,
        so even if the planner ignores the injected directive the dead step never executes — it
        fails with the reason, which the loop/replan feeds back. No constraints -> always None."""
        cons = getattr(self, "hard_constraints", None)
        if not cons or not isinstance(step, dict):
            return None
        op = step.get("op")
        salient = self._step_salient(step).lower()
        for c in cons:
            match = str(c.get("match") or "").strip().lower()
            if not match:
                continue
            c_op = c.get("op")                 # None -> applies to any op
            if c_op and c_op != op:
                continue
            if match not in salient:
                continue
            # A launch gate (almost always from a "broken Start-menu shortcut" fact) targets the
            # BARE-NAME launch that routes through Start-menu search and hits the broken shortcut.
            # A full-path / .exe / `ms-settings:` URI / system-command launch bypasses that path —
            # it's the legitimate workaround — so do NOT block it (else the app is unusable forever).
            if c_op == "launch" and op == "launch" and self._is_winr_target(step.get("app", "")):
                continue
            return c.get("reason") or f"{op} {match!r} is disallowed on this machine"
        return None

    # launch-misfire phrases: the keystrokes fell into Start-menu search or a browser search bar
    # instead of opening the app (a panel APPEARS, so the frame-diff fast-path would falsely accept
    # it). Live 2026-06-22: a full-path launch landed in Start search ('No results found for …') and
    # reported ok (runs/firefox_073814/06_launch.png).
    _SEARCH_MISFIRE = ("no results found", "no apps or documents", "search the web",
                       "see web results", 'results for "', "bing.com/search", "/search?q=")

    @staticmethod
    def _is_exe_path(app):
        """True if `app` is a full filesystem path to an .exe (quoted or not). These launch via cmd
        `start`, NOT Win+R — Win+R drops a spaced/quoted path into Start search / a focused browser."""
        a = str(app).strip().strip('"').strip().lower()
        return a.endswith(".exe") and ("\\" in a or "/" in a)

    @staticmethod
    def _exe_friendly_name(path):
        """'C:\\…\\firefox.exe' -> 'Firefox' for the vision launch-confirm."""
        base = os.path.basename(str(path).strip().strip('"'))
        if base.lower().endswith(".exe"):
            base = base[:-4]
        return Executive._APP_DISPLAY.get(base.lower(), (base[:1].upper() + base[1:]) if base else base)

    def _launch_misfired(self, png, app):
        """True if a launch attempt fell into Start-menu search / a browser search bar instead of
        opening the app — detected so launch() FAILS (and retries) instead of false-confirming on the
        frame change. Signs: a Start 'No results' panel, or a web search echoing the launch string."""
        txt = (self.verifier.read_text(png) or "").lower()
        if any(p in txt for p in self._SEARCH_MISFIRE):
            return True
        base = str(app).strip().strip('"').lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        return bool(base) and ("bing" in txt or "search?q=" in txt) and base in txt

    def _open_cmd(self):
        """Open a Command Prompt reliably (try Win+R, then Start search) and CONFIRM via vision.
        The agent has no shell on the target, so cmd is the reliable channel to launch an arbitrary
        exe path (and is robust to the intermittently-flaky Win+R)."""
        for via_winr in (True, False):
            if via_winr:
                self.env.r4.combo("win+r"); time.sleep(1.2)
                self.env.r4.type("cmd"); time.sleep(0.4); self.env.r4.key("enter"); time.sleep(1.8)
            else:
                self.env.r4.key("win"); time.sleep(1.3)
                self.env.r4.type("cmd"); time.sleep(1.3); self.env.r4.key("enter"); time.sleep(1.8)
            if self._app_open("cmd", self.observe()):
                return True
        return False

    def _launch_exe_path(self, path, s=3.0):
        """Launch a full EXE PATH via cmd `start` — the reliable HID channel for a spaced/quoted path
        (Win+R drops it into Start search / a focused browser). Opens cmd, runs
        `start "" "<path>" & exit` (the '& exit' closes cmd in one typed line, so we never alt+f4 the
        app that just launched), then CONFIRMS via vision (frame-diff alone false-confirms a panel)."""
        p = str(path).strip().strip('"')
        name = self._exe_friendly_name(p)
        for _ in range(2):
            if not self._open_cmd():
                self.dismiss_modal(1)
                continue
            self.env.r4.type(f'start "" "{p}" & exit'); time.sleep(0.5)
            self.env.r4.key("enter"); time.sleep(s)
            if self._app_open(name, self.observe()):
                return True
            self.dismiss_modal(1)
        return False

    def launch(self, app, s=2.5, retries=1):
        """Robust app launch. Keyboard-only; no taskbar grounding.

        ROUTING (three channels): a full EXE PATH ('C:\\…\\firefox.exe') -> cmd `start` (Win+R is
        unreliable for a spaced/quoted path — it falls into Start search / a focused browser, live
        2026-06-22); a SYSTEM command / `ms-settings:` URI -> Win+R; an INSTALLED GUI app NAME
        (Firefox, Chrome…) -> START-MENU SEARCH (Win+R can't run those by bare name — the live
        'Windows cannot find Firefox' blocker).
        CONFIRM: a 'cannot find' dialog OR a Start-search/browser MISFIRE is NEVER success (Esc +
        retry via Start search) — this kills the false-positive where a panel APPEARING was read as
        'the app opened'. Otherwise the frame-change fast path, then a vision 'is <app> open?' check."""
        app = str(app).strip()
        if self._is_exe_path(app):
            return self._launch_exe_path(app, s=max(s, 3.0))
        use_winr = self._is_winr_target(app)
        for attempt in range(retries + 1):
            before = self.observe()
            if use_winr:
                self.env.r4.combo("win+r"); time.sleep(1.2)
                self.env.r4.type(app); time.sleep(0.4)
                self.env.r4.key("enter"); time.sleep(s)
            else:
                self.env.r4.key("win"); time.sleep(1.3)    # open Start menu
                self.env.r4.type(app); time.sleep(1.4)     # let search results populate
                self.env.r4.key("enter"); time.sleep(s)    # launch the top hit
            after = self.observe()
            # FAILURE first (kills the false-positive): a 'cannot find' dialog, OR the keystrokes fell
            # into Start search / a browser search instead of opening the app (a panel appeared).
            if self._launch_error_dialog(after) or self._launch_misfired(after, app):
                self.dismiss_modal(2)                      # Esc the dialog / search panel
                use_winr = False                           # retry via Start search, not Win+R
                time.sleep(0.5)
                continue
            if self._changed(before, after, thresh=6.0):
                return True                       # fast path: a real window appeared (not a misfire)
            if self._app_open(app, after):
                return True                       # small change, but vision confirms it's open
            use_winr = False                      # didn't open -> try Start search on the retry
            time.sleep(0.8)
        return False

    # ---- visual grounding primitive (UI-TARS, stateless) ----
    @staticmethod
    def _bare_label(target_desc):
        """Strip a descriptive target to a bare noun label: drop a leading article and a
        trailing role word + everything after it. 'the Set default button in Settings' ->
        'Set default'. Used as the second grounding attempt."""
        s = re.sub(r"^\s*(the|a|an)\s+", "", target_desc.strip(), flags=re.I)
        s = re.sub(r"\s+(button|icon|option|item|tab|field|menu|link|heading|label|dropdown)\b.*$",
                   "", s, flags=re.I)
        return s.strip(" '\"")

    def ground(self, target_desc):
        """One stateless UI-TARS grounding for `target_desc` on the CURRENT frame.
        Returns (xy, action_str) or (None, None).

        UI-TARS is an AGENT, not a pure grounder. Handed a DESCRIPTIVE target ("the current
        default web browser button under the 'Web browser' heading") it reasons about it as a
        TASK and emits finished()/scroll instead of a click -> no coordinate -> a SILENT click
        failure. (Isolated 2026-06-21 on the Win10 default-browser task: that exact phrasing
        returned DONE, while "Microsoft Edge" / "click Microsoft Edge under Web browser"
        grounded cleanly to the same tile within ~50px — precision was never the issue, phrasing
        was.) So: force CLICK intent, and if the model still won't click (control token / no
        coord), retry ONCE on the SAME frame with a stripped bare label."""
        png = self.observe()
        bare = self._bare_label(target_desc)
        attempts = [f"Click {target_desc}."]
        if bare and bare.lower() != target_desc.strip().lower():
            attempts.append(f"Click {bare}.")
        tried = []
        self.last_ground = {"target": target_desc, "attempts": tried, "xy": None}
        for instruction in attempts:
            self.agent.reset()
            txt, actions = self.agent.predict(instruction, {"screenshot": png})
            tried.append({"instruction": instruction, "raw": (txt or "")[:300], "actions": actions})
            for a in actions:
                if a in ("DONE", "FAIL", "WAIT", "ANSWER"):
                    continue  # agent declined to click -> try the bare-label phrasing
                m = re.search(r"\((\d+),\s*(\d+)\)", a)
                if m:
                    xy = (int(m.group(1)), int(m.group(2)))
                    self.last_ground["xy"] = xy
                    return xy, a
        return None, None

    @staticmethod
    def _crop_around(png, xy, r=160):
        """Crop a square of `png` centered at `xy` (radius r) for a LOCALIZED vision/diff check.
        Returns PNG bytes; the original png unchanged if it can't be decoded."""
        try:
            import cv2
            import numpy as np
            arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                return png
            h, w = arr.shape[:2]
            x, y = int(xy[0]), int(xy[1])
            crop = arr[max(0, y - r):min(h, y + r), max(0, x - r):min(w, x + r)]
            ok, buf = cv2.imencode(".png", crop)
            return buf.tobytes() if ok else png
        except Exception:
            return png

    def _ground_ok(self, target, xy, png):
        """PRE-CLICK gate: verify the grounded point really IS `target` before committing the
        click — the direct fix for the live-run failure mode. UI-TARS returns *a* coordinate for
        any named target even when it isn't on the current screen (it grounded 'Firefox' to a
        taskbar icon while a browser new-tab page was showing), and the old global frame-diff then
        accepted the click because some pixels moved. We crop around the grounded point and ask the
        vision model whether that element is the target; a 'no' ABSTAINS instead of firing a
        confident wrong click. FAIL-OPEN: if there's no vision verifier (answer None) we do NOT
        block (return True) — never worse than before."""
        ans = self.verifier._vision(
            self._crop_around(png, xy),
            f"This is a small crop of a screen centered on where the agent is about to click. Is "
            f"the UI element at the center '{target}'? Answer only 'yes' or 'no'.")
        if ans is None:
            return True                     # cannot verify -> don't block
        return "yes" in ans.lower()

    def _click_effect(self, before, after, xy):
        """Did the click actually DO something where we clicked? A LOCALIZED diff around `xy` (a
        button highlights, a menu opens beneath it) is a far better success signal than the old
        global frame-diff, which fired on any unrelated repaint (a background browser, the taskbar
        clock) -> false 'ok'. Localized change wins; otherwise require a LARGER global change than
        the old gate so a stray flicker no longer counts as success."""
        loc = self._frame_diff(self._crop_around(before, xy), self._crop_around(after, xy))
        if loc > 3.5:
            return True
        return self._changed(before, after, thresh=8.0)   # higher bar than the old global 3.5

    def click_target(self, target_desc, retries=2, s=1.5):
        """Ground a target, VERIFY the ground, click it, and verify a LOCALIZED effect. Re-grounds
        on retry (stateless). The pre-click `_ground_ok` gate is the direct prevention for the live
        failure mode (a confident coordinate on the wrong screen + a global frame-diff that accepts
        any movement): an unverifiable ground now ABSTAINS — the step fails cleanly with a 'target
        not on screen' note so the planner can scroll or replan — instead of firing a wrong click
        whose side effects then have to be undone."""
        for attempt in range(retries + 1):
            before = self.observe()
            xy, _ = self.ground(target_desc)
            if xy is None:
                continue
            if not self._ground_ok(target_desc, xy, before):
                if isinstance(self.last_ground, dict):
                    self.last_ground["verified"] = False   # surfaced in _failure_summary
                continue                                   # do NOT commit a wrong-state click
            if isinstance(self.last_ground, dict):
                self.last_ground["verified"] = True
            self.env.r4.move(*xy); self.env.r4.click(); time.sleep(s)
            after = self.observe()
            if self._click_effect(before, after, xy):
                return True, xy
        return False, None

    # ---- verification ----
    def verify_text(self, expect):
        return self.verifier.has_text(self.observe(), expect)

    # ---- safety: stray-modal handling + focus confirmation ----
    # (Lesson from the live Excel run: a Windows-Update modal stole focus, and blindly
    #  sending Enter for the next cell activated its default 'Restart now' -> the target
    #  rebooted. Rule: dismiss unknown dialogs with Esc ONLY, and never type until the
    #  intended window is confirmed foreground.)
    def confirm(self, question: str) -> "bool|None":
        """Vision yes/no about the current screen. None if no vision verifier."""
        ans = self.verifier._vision(self.observe(), question + " Answer only 'yes' or 'no'.")
        return None if ans is None else ("yes" in ans.lower())

    def dismiss_modal(self, tries: int = 2):
        """Clear a stray popup with Esc ONLY — never Enter (Enter rebooted the box once).
        Harmless no-op on a normal screen."""
        for _ in range(tries):
            self.env.r4.key("esc"); time.sleep(0.6)

    def launch_verified(self, app: str, confirm_question: str, s: float = 8.0, tries: int = 2):
        """Win+R launch, then CONFIRM the app is actually foreground (vision) before
        returning True. On failure, dismiss any stray modal (e.g. a 'cannot find' error or
        an update popup) and retry. Prevents typing into nothing / the wrong window."""
        for _ in range(tries):
            self.env.r4.combo("win+r"); time.sleep(1.2)
            self.env.r4.type(app); time.sleep(0.4)
            self.env.r4.key("enter"); time.sleep(s)
            if self.confirm(confirm_question):
                return True
            self.dismiss_modal()
        return False

    # ---- reset to a clean desktop (VISION-gated, robust to identical stacked windows) ----
    def desktop_is_clear(self):
        """Vision check: just the desktop (wallpaper+taskbar), no app windows/dialogs?
        Frame-diff CANNOT tell identical stacked windows apart (closing 1 of 11 empty
        Notepads barely changes the frame) — the vision model can, so it drives reset."""
        ans = self.verifier._vision(
            self.observe(),
            "Look at this Windows screen. Is any application window or dialog box open "
            "(e.g. Notepad, Calculator, a 'save changes?' box), or is it ONLY the empty "
            "desktop wallpaper with the taskbar? Answer with one word: 'window' or 'empty'.")
        return (ans is not None) and ("empty" in ans.lower()) and ("window" not in ans.lower())

    def reset_clean(self, max_close=25, ground_after=4):
        """Close the frontmost window repeatedly until the vision verifier says the
        desktop is clear. Robust to a stack of identical windows (the bug that piled up
        11 Notepads: frame-diff saw 'no change' closing 1 of N identical and gave up).
        The loop is self-correcting: if a keyboard close races/misses, vision still sees
        a window and we try again. Falls back to grounding the X if the keyboard close
        makes no progress for `ground_after` consecutive iterations (a truly stuck window).
        Returns a small status dict — NEVER raw frames (echo-safe)."""
        closed = 0
        stuck = 0
        for _ in range(max_close):
            if self.desktop_is_clear():
                return {"cleared": True, "closed": closed}
            before = self.observe()
            # keyboard close of the frontmost window: system menu (Alt+Space) -> Close (c);
            # generous settle so the menu is up before 'c'; Alt+N dismisses a save prompt.
            self.env.r4.combo("alt+space"); time.sleep(1.0)
            self.env.r4.key("c"); time.sleep(1.0)
            self.env.r4.combo("alt+n"); time.sleep(0.9)
            if self._changed(before, self.observe(), thresh=8.0):
                closed += 1; stuck = 0
            else:
                stuck += 1
                if stuck >= ground_after:   # keyboard stuck -> ground the X
                    ok, _ = self.click_target("the X close button at the top-right of the "
                                              "frontmost window", retries=1)
                    if ok:
                        self.env.r4.combo("alt+n"); time.sleep(0.9)
                        closed += 1
                    stuck = 0
        return {"cleared": self.desktop_is_clear(), "closed": closed}

    # ---- per-run observability (preempts blind failures: log a frame + raw grounder output) ----
    def _make_run_dir(self, run_tag):
        """Create a per-run folder for step frames + the JSON log. Returns path or None."""
        try:
            d = os.path.join(self.runs_root, f"{run_tag}_{time.strftime('%H%M%S')}")
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            return None

    def _save_step_frame(self, run_dir, idx, op, xy=None):
        """Save the current frame for step `idx` (red crosshair at `xy` for clicks, so a
        misground is visible at a glance). Best-effort; never raises into the run loop."""
        try:
            import cv2
            import numpy as np
            arr = cv2.imdecode(np.frombuffer(self.observe(), np.uint8), cv2.IMREAD_COLOR)
            if xy is not None:
                x, y = int(xy[0]), int(xy[1])
                cv2.drawMarker(arr, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 44, 3)
                cv2.circle(arr, (x, y), 24, (0, 0, 255), 2)
            cv2.imwrite(os.path.join(run_dir, f"{idx:02d}_{op}.png"), arr)
        except Exception:
            pass

    # ---- failure diagnosis (negative observation handed to the planner's replan) ----
    def _failure_summary(self, rec):
        """Compact, model-readable explanation of a failed step PLUS what is actually on screen
        now. Fed to the planner as 'negative observation' so replan recovers from the real state
        instead of a bare 'click failed' — and so a verify failure reports what it saw, not just a
        bool. Reads screen text via the cheap tesseract path (vision fallback) once, on failure."""
        op = rec.get("op")
        step = rec.get("step", {}) or {}
        tgt = (step.get("target") or step.get("app") or step.get("text") or step.get("combo")
               or step.get("key") or step.get("ask") or step.get("expect") or step.get("number=="))
        head = f"step {rec.get('i')} ({op}{' ' + repr(tgt) if tgt else ''})"
        why = []
        if rec.get("error"):
            why.append(f"raised {rec['error']}")
        elif op == "click":
            g = rec.get("ground") or {}
            xy = g.get("xy")
            if g.get("verified") is False:
                why.append(f"the target was NOT found on the current screen (vision could not "
                           f"confirm {tgt!r} at the grounded point) — it may be off-screen "
                           f"(scroll to bring it into view) or the wrong window/screen is showing")
            elif xy:
                why.append(f"clicked {tuple(xy)} but nothing changed there (wrong target, or no "
                           f"such element on this screen)")
            else:
                why.append("the grounder returned no click coordinate (it refused to click that "
                           "target) — try a shorter visual label or a keyboard path")
        elif op == "launch":
            why.append(f"could not confirm {step.get('app')!r} opened — it may not be launchable "
                       "by that name via Win+R, or an error dialog appeared")
        elif op == "verify":
            if "got" in rec:
                why.append(f"display read {rec.get('got')!r}, expected {step.get('number==')!r}")
            elif rec.get("ask") is not None:
                why.append(f"the vision check answered NO to {step.get('ask')!r}")
            elif rec.get("verify") is not None:
                why.append(f"the text {step.get('expect')!r} was not found on screen")
            else:
                why.append("the check did not pass (nothing could read the screen)")
        else:
            why.append("failed")
        try:    # what IS on screen now — the observation the planner reasons from
            txt = re.sub(r"\s+", " ", (self.verifier.read_text(self.observe()) or "")).strip()
        except Exception:
            txt = ""
        out = f"{head} failed: {'; '.join(why)}."
        if txt:
            out += f" Screen now shows (text excerpt): {txt[:280]!r}"
        return out

    # ---- single-step execution (shared by run_plan AND the per-step closed loop) ----
    def _run_one_step(self, step, i, t0, run_dir=None, on_event=None):
        """Run exactly ONE plan step and return (rec, control) where control is:
            "continue" — step ran ok, keep going
            "fail"     — step failed (rec carries ok=False + error/diagnosis)
            "done"     — the step was {op:done}
        This is the lossless extraction of run_plan's old per-step body — same guards, same
        dispatch, same per-step frame/event side effects — so run_plan is behavior-identical AND
        the closed loop (run_step) reuses the EXACT same step semantics. The caller appends `rec`
        to its own log/history and acts on `control`."""
        op = step.get("op")
        rec = {"i": i, "op": op, "step": step, "t": round(time.time() - t0, 1)}

        # (0) HARD-CONSTRAINT gate — block a step that violates a recalled prohibition BEFORE it
        #     acts (empty constraints -> no-op, so the benchmark/normal runs are unaffected).
        blocked = self._blocked_by_constraint(step)
        if blocked:
            rec["ok"] = False
            rec["blocked"] = blocked
            rec["error"] = f"blocked by hard constraint: {blocked}"
            if run_dir:
                self._save_step_frame(run_dir, i, op)
            if on_event:
                on_event(f"gate: BLOCKED {op} — {str(blocked)[:60]}")
            return rec, "fail"

        ok = True
        # ---- closed-loop pre-step guards (run BEFORE the action) ----
        # (a) clear a stale blocking ERROR dialog before a click (benchmark has no clicks -> no-op)
        if self.guard_dialogs and op == "click" and self._blocking_dialog():
            self.env.r4.key("esc"); time.sleep(0.6)
            rec["dismissed_dialog"] = True
            if on_event:
                on_event("cleared a blocking error dialog before clicking")
        # (b) optional per-step precondition: confirm the expected context, else FAIL the step
        #     (opt-in — steps without 'precondition' are unaffected; None vision -> fail-open)
        pre = step.get("precondition")
        if pre and self.confirm(pre) is False:
            rec["ok"] = False
            rec["error"] = f"precondition not met ({pre!r}) — the expected window/screen was not visible"
            if run_dir:
                self._save_step_frame(run_dir, i, op)
            if on_event:
                on_event(f"{op}: precondition NOT met — {str(pre)[:50]}")
            return rec, "fail"
        try:
            if op == "launch":
                ok = self.launch(step["app"])
            elif op == "type":
                self.type_text(step["text"]); ok = True
            elif op == "key":
                self.key_combo(step["combo"]); ok = True
            elif op == "tap":
                self.tap(step["key"]); ok = True
            elif op == "scroll":
                self.scroll(step.get("direction", "down"), step.get("amount", 3)); ok = True
            elif op == "click":
                ok, rec["xy"] = self.click_target(step["target"])
                rec["ground"] = self.last_ground   # instructions tried + raw model output
            elif op == "sleep":
                time.sleep(step.get("secs", 1.0)); ok = True
            elif op == "verify":
                if "number==" in step:
                    got = self.verifier.read_number(self.observe())
                    rec["got"] = got
                    ok = (got is not None) and (str(got) == str(step["number=="]))
                elif "ask" in step:
                    # semantic STATE check via the vision model (yes/no). Use for goal
                    # states that never appear as a literal on-screen string (e.g. "is
                    # Chrome the default browser?"); the 'expect' substring path below
                    # CANNOT pass those — it matches the planner's sentence against screen
                    # text. (Isolated 2026-06-21: the live default-browser run died here on
                    # expect="Google Chrome is now the default web browser".)
                    res = self.confirm(step["ask"])
                    rec["ask"] = res
                    ok = bool(res)  # None (no verifier) counts as not-verified
                else:
                    res = self.verify_text(step["expect"])
                    rec["verify"] = res
                    ok = bool(res)  # None (no verifier) counts as not-verified
            elif op == "done":
                if on_event:
                    on_event("done")
                return rec, "done"
            else:
                ok = False; rec["error"] = f"unknown op {op!r}"
        except Exception as e:
            ok = False; rec["error"] = repr(e)
        rec["ok"] = ok
        if run_dir:
            self._save_step_frame(run_dir, i, op, xy=rec.get("xy"))
        if on_event:
            d = str(step.get("app") or step.get("text") or step.get("target") or
                    step.get("combo") or step.get("key") or step.get("expect") or
                    step.get("ask") or step.get("number==") or "")[:40]
            m = (f"{op} {d}").strip() + ("  ok" if ok else "  FAILED")
            if op == "verify" and "got" in rec:
                m += f" (read {rec['got']})"
            on_event(m)
        return rec, ("continue" if ok else "fail")

    def run_step(self, step, i=0, t0=None, run_dir=None, on_event=None):
        """Execute ONE step for the per-step closed loop (run_goal_step). Returns
        {status: "ok"|"done"|"failed@i:op", op, rec, [failure_summary]}. Does NOT touch self.log
        (the closed loop keeps its own history); on failure it attaches the same on-screen
        diagnosis the replan path uses, so the planner reacts to the real state next turn."""
        t0 = t0 if t0 is not None else time.time()
        rec, control = self._run_one_step(step, i, t0, run_dir=run_dir, on_event=on_event)
        if control == "done":
            status = "done"
        elif control == "fail":
            status = f"failed@{i}:{rec.get('op')}"
        else:
            status = "ok"
        out = {"status": status, "op": rec.get("op"), "rec": rec}
        if status.startswith("failed"):
            try:
                out["failure_summary"] = self._failure_summary(rec)
            except Exception as e:
                out["failure_summary"] = f"step failed ({status}); could not summarize ({e!r})"
        return out

    # ---- plan execution ----
    def run_plan(self, plan, goal=None, run_tag="plan", on_event=None):
        """Execute a structured plan (list of step dicts). Returns a result dict.
        Step ops: launch{app}, type{text}, key{combo}, tap{key}, click{target},
                  verify{expect|ask|number==}, sleep{secs}, done.
        With self.capture (default on) a per-step frame + the grounder's raw output are saved to
        a per-run folder, so any failure is diagnosable from the log without a bespoke rerun."""
        t0 = time.time()
        self.log = []
        status = "incomplete"
        run_dir = self._make_run_dir(run_tag) if self.capture else None
        for i, step in enumerate(plan):
            rec, control = self._run_one_step(step, i, t0, run_dir=run_dir, on_event=on_event)
            self.log.append(rec)
            if control == "done":
                status = "done"
                break
            if control == "fail":
                status = f"failed@{i}:{rec.get('op')}"
                break
        result = {"status": status, "elapsed": round(time.time() - t0, 1),
                  "goal": goal, "steps": len(self.log), "log": self.log, "run_dir": run_dir}
        # on failure, attach a compact diagnosis + on-screen text for the planner's replan
        if str(status).startswith("failed") and self.log:
            try:
                result["failure_summary"] = self._failure_summary(self.log[-1])
            except Exception as e:
                result["failure_summary"] = f"step failed ({status}); could not summarize ({e!r})"
        # write the JSON next to the frames (run_dir) and into log_dir if separately set
        targets = []
        if run_dir:
            targets.append(os.path.join(run_dir, "plan.json"))
        if self.log_dir and self.log_dir != run_dir:
            try:
                os.makedirs(self.log_dir, exist_ok=True)
                targets.append(os.path.join(self.log_dir, f"{run_tag}_{time.strftime('%H%M%S')}.json"))
            except Exception:
                pass
        for p in targets:
            try:
                json.dump(result, open(p, "w"), indent=2)
            except Exception:
                pass
        return result
