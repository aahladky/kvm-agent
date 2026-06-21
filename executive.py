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

os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

_OLLAMA = os.environ.get("OLLAMA_HOST", "http://192.168.0.155:11434")


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
            import pytesseract  # noqa
            from PIL import Image  # noqa
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
        """True/False if we could check; None if no verifier is available."""
        if self._tess is not None:
            txt = self.read_text(png)
            return expect.lower() in txt.lower()
        ans = self._vision(
            png, f"Does this screen show the text '{expect}'? Answer ONLY 'yes' or 'no'.")
        if ans is None:
            return None
        return "yes" in ans.lower()

    def read_number(self, png: bytes) -> "str|None":
        """Best-effort: the most prominent number on screen (e.g. a calculator display)."""
        if self._tess is not None:
            txt = self.read_text(png)
            nums = re.findall(r"-?\d[\d,]*\.?\d*", txt)
            nums = [n.replace(",", "") for n in nums]
            return max(nums, key=len) if nums else None
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


# ──────────────────────────────────────────────────────────── executive
class Executive:
    def __init__(self, env, agent, verifier=None, log_dir=None, settle=1.0):
        self.env = env
        self.agent = agent           # UITARSAgent (the stateless executor)
        self.verifier = verifier or Verifier()
        self.settle = settle
        self.log_dir = log_dir
        self.log = []

    # -- construction for standalone use (opens the camera + Pico itself) --
    @classmethod
    def open(cls, executor_model="uitars-q4", **kw):
        from pico_env import PicoEnv
        from cua_agent import make_agent
        env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
        agent = make_agent("uitars", model=executor_model, history=1,
                           temperature=0.0, screen_size=(1920, 1080))
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

    # display names for the vision launch-confirm: Win+R takes the exe name, but the
    # window the verifier sees shows the friendly name (calc -> "Calculator").
    _APP_DISPLAY = {"calc": "Calculator", "notepad": "Notepad", "mspaint": "Paint",
                    "explorer": "File Explorer", "cmd": "Command Prompt",
                    "write": "WordPad", "wordpad": "WordPad"}

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

    def launch(self, app, s=2.5, retries=1):
        """Robust app launch: Win+R -> app -> Enter. Keyboard-only; no taskbar grounding.
        Confirms the app opened. FAST PATH: a large frame change (a window over the
        desktop, e.g. Notepad ~75 mean-diff) returns immediately. FALLBACK: when the
        change is small, a vision check ('is <app> open?') confirms it. This replaces the
        fragile single frame-diff gate that, with Calculator-over-maximized-Notepad
        scoring 4.64 < 6.0, reported launch failure -> aborted the multi-app plan before
        the type step (display stuck at 0) AND retry-launched calc a second time ("opened
        twice"). Vision now confirms on the first attempt; retries only if BOTH say no."""
        for attempt in range(retries + 1):
            before = self.observe()
            self.env.r4.combo("win+r"); time.sleep(1.2)
            self.env.r4.type(app); time.sleep(0.4)
            self.env.r4.key("enter"); time.sleep(s)
            after = self.observe()
            if self._changed(before, after, thresh=6.0):
                return True                       # fast path: unambiguous visible change
            if self._app_open(app, after):
                return True                       # small change, but the app IS open (vision)
            time.sleep(0.8)  # let any stray state settle before retrying
        return False

    # ---- visual grounding primitive (UI-TARS, stateless) ----
    def ground(self, target_desc):
        """One stateless UI-TARS grounding for `target_desc` on the CURRENT frame.
        Returns (xy, action_str) or (None, None)."""
        png = self.observe()
        self.agent.reset()
        _txt, actions = self.agent.predict(target_desc, {"screenshot": png})
        for a in actions:
            m = re.search(r"\((\d+),\s*(\d+)\)", a)
            if m:
                return (int(m.group(1)), int(m.group(2))), a
        return None, None

    def click_target(self, target_desc, retries=2, s=1.5):
        """Ground a visual target and click it; verify the frame changed. Re-ground on
        retry (stateless, so each attempt re-observes — no stale-coordinate copying)."""
        for attempt in range(retries + 1):
            before = self.observe()
            xy, _ = self.ground(target_desc)
            if xy is None:
                continue
            self.env.r4.move(*xy); self.env.r4.click(); time.sleep(s)
            after = self.observe()
            if self._changed(before, after):
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

    # ---- plan execution ----
    def run_plan(self, plan, goal=None, run_tag="plan", on_event=None):
        """Execute a structured plan (list of step dicts). Returns a result dict.
        Step ops: launch{app}, type{text}, key{combo}, tap{key}, click{target},
                  verify{expect|number==}, sleep{secs}, done."""
        t0 = time.time()
        self.log = []
        status = "incomplete"
        for i, step in enumerate(plan):
            op = step.get("op")
            rec = {"i": i, "op": op, "step": step, "t": round(time.time() - t0, 1)}
            ok = True
            try:
                if op == "launch":
                    ok = self.launch(step["app"])
                elif op == "type":
                    self.type_text(step["text"]); ok = True
                elif op == "key":
                    self.key_combo(step["combo"]); ok = True
                elif op == "tap":
                    self.tap(step["key"]); ok = True
                elif op == "click":
                    ok, rec["xy"] = self.click_target(step["target"])
                elif op == "sleep":
                    time.sleep(step.get("secs", 1.0)); ok = True
                elif op == "verify":
                    if "number==" in step:
                        got = self.verifier.read_number(self.observe())
                        rec["got"] = got
                        ok = (got is not None) and (str(got) == str(step["number=="]))
                    else:
                        res = self.verify_text(step["expect"])
                        rec["verify"] = res
                        ok = bool(res)  # None (no verifier) counts as not-verified
                elif op == "done":
                    status = "done"; self.log.append(rec)
                    if on_event: on_event("done")
                    break
                else:
                    ok = False; rec["error"] = f"unknown op {op!r}"
            except Exception as e:
                ok = False; rec["error"] = repr(e)
            rec["ok"] = ok
            self.log.append(rec)
            if on_event:
                d = str(step.get("app") or step.get("text") or step.get("target") or
                        step.get("combo") or step.get("key") or step.get("expect") or
                        step.get("number==") or "")[:40]
                m = (f"{op} {d}").strip() + ("  ok" if ok else "  FAILED")
                if op == "verify" and "got" in rec:
                    m += f" (read {rec['got']})"
                on_event(m)
            if not ok:
                status = f"failed@{i}:{op}"
                break
        result = {"status": status, "elapsed": round(time.time() - t0, 1),
                  "goal": goal, "steps": len(self.log), "log": self.log}
        if self.log_dir:
            try:
                os.makedirs(self.log_dir, exist_ok=True)
                p = os.path.join(self.log_dir, f"{run_tag}_{time.strftime('%H%M%S')}.json")
                json.dump(result, open(p, "w"), indent=2)
            except Exception:
                pass
        return result
