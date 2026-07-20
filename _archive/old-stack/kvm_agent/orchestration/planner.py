"""
planner.py — pluggable PLANNER layer for the rig (the brain that decomposes goals).

The planner turns a natural-language goal into a structured PLAN of atomic steps that
executive.Executive knows how to run, and RE-PLANS from the live screen when a step
fails. Separating this from the 7B executor is the core architectural fix: the planner
does long-horizon reasoning/state-tracking (its strength); UI-TARS does single-step
grounding (its strength).

Implementations (swap freely; all emit the same plan schema):
  - ClaudePlanner : Anthropic API. The strong baseline you asked to start with. Needs
                    ANTHROPIC_API_KEY. Sees the current screenshot, so it can plan from
                    real state and recover from failures.
  - LocalPlanner  : an OpenAI-compatible local endpoint — the all-local target. Point it
                    at a reasoning model served on the desktop B580 (e.g. llama.cpp/vLLM
                    with --api). Same prompt/contract as ClaudePlanner; this is the
                    drop-in for the "all local" end state.
  - RulePlanner   : zero-dependency deterministic decomposition for the common patterns
                    ("open <app> and type <text>", "compute <expr> in Calculator").
                    Used as an offline fallback and to isolate EXECUTIVE reliability from
                    planner variability in the K-rep measurement.

PLAN SCHEMA — a JSON list of steps. Ops the executive implements:
  {"op":"launch","app":"notepad"}        # Win+R app-launch (keyboard; no taskbar click)
  {"op":"type","text":"..."}             # type a string over HID
  {"op":"tap","key":"enter"}             # one named key (enter/esc/tab/backspace/arrows)
  {"op":"key","combo":"ctrl+s"}          # a hotkey combo
  {"op":"click","target":"the Save button"}   # UI-TARS grounding+click (visual targets only)
  {"op":"verify","expect":"59"}          # confirm literal text is on screen (OCR/vision)
  {"op":"verify","ask":"Is Chrome the default browser?"}  # vision yes/no for a STATE not shown as literal text
  {"op":"verify","number==":"59"}        # confirm the prominent number == value (calc display)
  {"op":"scroll","direction":"down","amount":3}  # wheel-scroll to bring an off-screen target into view
  {"op":"sleep","secs":1.0}
  {"op":"done"}                          # goal complete
Any ACTION step may add an optional "precondition":"<yes/no description of the expected
window/screen>" — it is confirmed on-screen before the step runs; the step fails and re-plans if
that context is not visible (so you never act in the wrong window or a stray dialog).
Prefer keyboard ops; use click only when there is no keyboard path. Always end with a
verify of the goal state, then done.
"""
import os, re, json, time, base64

PLAN_SCHEMA_DOC = __doc__.split("PLAN SCHEMA")[1]

SYSTEM = (
    "You are the PLANNER for a computer-use agent that drives a real Windows desktop "
    "through a hardware keyboard/mouse injector. You output ONLY a JSON array of steps "
    "(no prose, no markdown fence). Prefer keyboard actions; launch apps with the "
    "'launch' op (never by clicking the taskbar). Use 'click' with a short visual target "
    "description ONLY for things with no keyboard path. Always verify the goal on screen "
    "before 'done'.\n\n"
    "IDIOMS (follow these exactly):\n"
    "- Calculator arithmetic: TYPE the whole expression then press Enter (type '6*7', then "
    "tap 'enter'). NEVER click keypad buttons.\n"
    "- Fill a formula down a column: do NOT enter cells one-by-one and do NOT drag a fill "
    "handle. Select the range and fill in one shot: key 'ctrl+g' (Go To); type the range "
    "like 'G5:G300'; tap 'enter'; type the first-row formula like '=F5-E5'; key "
    "'ctrl+enter'. Overshoot the row count and IF-guard the formula "
    "(=IF(E5=\\\"\\\",\\\"\\\",F5-E5)) so blank rows stay empty.\n"
    "- Keep plans COMPACT — never unroll a per-row loop into many steps.\n"
    "- Install software (a browser/app): do NOT download via a browser and click through an "
    "installer wizard — that path is fragile. Launch a terminal and use the package manager: "
    "launch 'cmd'; type a winget line like 'winget install --silent "
    "--accept-package-agreements --accept-source-agreements Mozilla.Firefox'; tap 'enter'; "
    "sleep ~40s for the install; then verify. Keyboard-only and deterministic.\n"
    "- IMPORTANT: a browser/app you JUST winget-installed is NOT yet a registered Windows "
    "default-app CHOICE — it will not appear in ms-settings:defaultapps until its first launch. "
    "To make it default: first 'launch' it once BY ITS FRIENDLY NAME (launch 'Firefox', NOT the "
    "winget id 'Mozilla.Firefox' — the launcher opens an installed app via Start-menu search) to "
    "register it (its first-run window usually has "
    "a 'Make Default'/'Set as default' button — clicking that is the most reliable path), THEN "
    "open ms-settings:defaultapps and pick it. Close/minimize the terminal first (key 'alt+f4' "
    "while cmd is focused) so it doesn't cover the window.\n"
    "- If launching an installed app BY NAME is blocked (a recalled HARD CONSTRAINT) or its Start "
    "shortcut is broken, do NOT retry the bare name and do NOT type a path into a browser. Launch it "
    "BY ITS FULL EXE PATH (the executive opens a full path reliably via cmd) — but FIRST get the REAL "
    "path: a winget app is often a USER-scope install under %LOCALAPPDATA%, NOT C:\\Program Files, so "
    "don't assume the path. In cmd, run  reg query "
    "\"HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\firefox.exe\" /ve  (and the "
    "HKLM hive) — the (Default) value is the full exe path; if both are empty, search  where /r "
    "\"%LOCALAPPDATA%\" firefox.exe  then  where /r C:\\ firefox.exe  (whole drive — slower; sleep "
    "and re-read the screen for the result). READ the printed path off the screen, then launch THAT "
    "exact path. Never re-type a path cmd reported it 'cannot find'.\n"
    "- If winget is truly unavailable you must download the installer AND THEN run the "
    "downloaded .exe and click through it (Next/Install/Finish) — downloading is NOT installing.\n"
    "- Set a Windows default app (e.g. the default browser): open the Settings page directly "
    "with launch 'ms-settings:defaultapps' (Win+R runs ms-settings: URIs), then set the default "
    "there — prefer this over an app's own 'make default' button.\n"
    "- Cell refs: account for the table's real position on screen (a leading spacer column "
    "and title rows above the header are common, so the data may start below row 1 / right "
    "of column A).\n"
    "- Off-screen targets: if a target is likely BELOW the visible area (a settings row, a long "
    "list or page), emit a 'scroll' op to bring it into view BEFORE clicking it, e.g. {\"op\":"
    "\"scroll\",\"direction\":\"down\",\"amount\":3}; then click the now-visible target.\n"
    "- Right window: for a step that needs a specific app/window foreground (typing into an app, "
    "clicking inside Settings), add an optional 'precondition' yes/no description, e.g. {\"op\":"
    "\"type\",\"text\":\"...\",\"precondition\":\"the Notepad window is open and focused\"}; the step "
    "is skipped + re-planned if that context isn't on screen, so you never act on the wrong window.\n"
    "- click targets: name the element with a SHORT label (e.g. 'Microsoft Edge', 'the Set "
    "default button', 'Google Chrome') — NEVER a descriptive clause or a question. The "
    "executor clicks exactly what you name; a sentence makes it stop instead of clicking.\n"
    "- verify ops: use 'expect' ONLY for text you KNOW appears verbatim on screen; for a STATE "
    "that isn't literal on-screen text use 'ask' with a yes/no question (e.g. {\"op\":\"verify\","
    "\"ask\":\"Is Google Chrome the default browser?\"}); 'number==' for a calculator display. "
    "Never put a whole sentence in 'expect'.\n\n"
    "PLAN SCHEMA" + PLAN_SCHEMA_DOC
)


def _strip_reasoning_and_fences(text):
    """Strip a <think>…</think> reasoning trace (and a stray split close tag) plus a ```code fence
    from a model reply, leaving the JSON. Shared by the array parser (_extract_json) and the
    single-object parser (_extract_step) so both tolerate reasoning models identically."""
    text = (text or "").strip()
    text = re.sub(r"(?is)<think>.*?</think>", "", text).strip()   # drop closed reasoning blocks
    if "</think>" in text:                                         # stray close tag -> keep the tail
        text = text.split("</think>")[-1].strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    return text


def _extract_json(text):
    """Pull a JSON array out of a model reply, tolerating fences/prose/reasoning.

    A reasoning model (Qwen3-VL-*-Thinking) prepends a <think>…</think> trace before the plan,
    and that trace routinely contains brackets — which the greedy array match below would
    swallow, producing invalid JSON. So strip the reasoning FIRST (see
    _strip_reasoning_and_fences). Harmless when there is no reasoning (non-thinking models)."""
    text = _strip_reasoning_and_fences(text)
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else json.loads(text)


def _first_json_object(text):
    """Return the first brace-balanced {...} object parsed from `text`, or None. String-aware, so a
    brace inside a quoted value (or trailing prose after the object) doesn't break the scan."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:j + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _extract_step(text):
    """Parse the SINGLE next action (one op dict) from a closed-loop reply. Accepts a bare object,
    a 1-element array, or an object embedded in prose/reasoning. Falls back to {op:done} only if
    nothing parses (the loop then re-asks rather than executing garbage)."""
    text = _strip_reasoning_and_fences(text)
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v[0]
    except Exception:
        pass
    obj = _first_json_object(text)
    if isinstance(obj, dict):
        return obj
    try:
        arr = _extract_json(text)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            return arr[0]
    except Exception:
        pass
    return {"op": "done"}


class Planner:
    def decompose(self, goal, screen_png=None):
        raise NotImplementedError

    def replan(self, goal, result, screen_png=None, history=None):
        """Given the failed run + the CURRENT screen, ask for a fresh plan that recovers.

        `history` is the list of prior-attempt summaries built by run_goal (every attempt so far
        and WHY it failed, oldest first, most recent last). Feeding the FULL history — not just the
        latest result — is the fix for the planner's amnesia: previously each replan saw only the
        last result, re-derived the plan blind, and could re-emit the exact step that just failed
        (the Win+R relaunch loop). The summaries carry the executive's on-screen observations, so
        the model recovers from the real state. Subclasses supply the model via _complete()."""
        hist = [h for h in (history or []) if h]
        if not hist:
            hist = [summarize_result(result)]
        latest, prior = hist[-1], hist[:-1]
        blocks = [f"GOAL: {goal}", ""]
        if prior:
            blocks.append("PREVIOUS ATTEMPTS (oldest first) — these approaches ALREADY FAILED; "
                          "do NOT repeat them:")
            blocks += [f"  - {h}" for h in prior]
            blocks.append("")
        blocks.append(f"MOST RECENT FAILURE: {latest}")
        blocks.append(
            "\nFirst diagnose WHY the previous attempt failed, then look at the CURRENT screen and "
            "output a NEW plan (JSON array) that recovers from where we are and completes the goal. "
            "Use a DIFFERENT approach for the step that failed — do not repeat an action that just "
            "failed, and do not relaunch a window that is already open.")
        return _extract_json(self._complete("\n".join(blocks), screen_png))

    def _complete(self, user_msg, screen_png=None):
        raise NotImplementedError

    def _inject(self, user_msg):
        """Prepend recalled memory (set by run_goal on self.context) to the user message. The
        planner stays a single completion — the ORCHESTRATOR does the recall and arms self.context;
        the planner never calls a memory tool itself. No-op if no context is armed."""
        ctx = getattr(self, "context", None)
        return (ctx + "\n\n" + user_msg) if ctx else user_msg

    def next_step(self, goal, screen_png=None, history=None):
        """CLOSED-LOOP single-action planning: given the GOAL, the LIVE screen, and a short
        history of what already happened this run, return the SINGLE next step (one op dict).

        This is the per-turn brain of run_goal_step — the alternative to decompose()'s up-front
        N-step plan. Because the planner is re-asked from the CURRENT frame every turn, it reacts
        to real state (Esc the broken-shortcut dialog the turn it appears) instead of running a
        stale plan into a changed screen. Reuses _complete (so _inject still prepends recalled
        memory / hard-constraint directives). Subclasses supply the model via _complete()."""
        hist = [h for h in (history or []) if h]
        blocks = [f"GOAL: {goal}", ""]
        if hist:
            blocks.append("ACTIONS SO FAR this run (most recent last) — what already happened:")
            blocks += [f"  - {h}" for h in hist[-8:]]
            blocks.append("")
        blocks.append(
            "Look at the CURRENT screen and output the SINGLE next action that best advances the "
            "goal, as ONE JSON object (NOT an array), e.g. {\"op\":\"launch\",\"app\":\"notepad\"} "
            "or {\"op\":\"click\",\"target\":\"Save\"}. Do only the next concrete step — do NOT plan "
            "ahead or emit a list. If a recalled HARD CONSTRAINT forbids an approach, choose a "
            "different one. If the goal is already satisfied on the CURRENT screen, output "
            "{\"op\":\"done\"}. Output only the JSON object, no prose.")
        return _extract_step(self._complete("\n".join(blocks), screen_png))


class ClaudePlanner(Planner):
    """Anthropic API planner. Strong reasoning + recovery; sees the screenshot.

    thinking=True enables Anthropic extended thinking (a reasoning pass before the answer);
    last_reasoning keeps that trace for the planner.json log. The thinking budget must be < the
    max_tokens budget, so a thinking planner should be built with a large max_tokens."""

    def __init__(self, model="claude-opus-4-8", api_key=None, max_tokens=4000, thinking=False):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.last_raw = None
        self.last_reasoning = None
        self.context = None        # recalled memory armed by run_goal (see _inject)

    def decompose(self, goal, screen_png=None):
        return _extract_json(self._complete(f"GOAL: {goal}\n\nOutput the plan.", screen_png))

    def _complete(self, user_msg, screen_png=None):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        content = [{"type": "text", "text": self._inject(user_msg)}]
        if screen_png:
            content.insert(0, {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(screen_png).decode()}})
        kw = {"model": self.model, "max_tokens": self.max_tokens, "system": SYSTEM,
              "messages": [{"role": "user", "content": content}]}
        if self.thinking:   # budget must stay under max_tokens; leave headroom for the JSON answer
            kw["thinking"] = {"type": "enabled",
                              "budget_tokens": max(1024, min(self.max_tokens - 1024, 8000))}
        r = client.messages.create(**kw)
        # with thinking on, content[0] is a thinking block (no .text) — pull blocks out by type
        self.last_reasoning = " ".join(
            getattr(b, "thinking", "") for b in r.content
            if getattr(b, "type", "") == "thinking").strip() or None
        texts = [getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text"]
        self.last_raw = texts[-1] if texts else ""   # the plan is the last text block
        return self.last_raw


class LocalPlanner(Planner):
    """All-local target: an OpenAI-compatible reasoning model (e.g. served on the B580).

    Deploy on the desktop B580 (Intel Arc, 12GB): serve a reasoning model with an
    OpenAI-compatible /v1 endpoint — llama.cpp `llama-server` (Vulkan/SYCL backend runs
    on Arc) or vLLM/IPEX — then point base_url here. Vision is optional: if the served
    model is text-only, pass send_image=False and the planner reasons from the goal +
    executive feedback alone (the executive's verify ops still ground truth on-screen)."""

    def __init__(self, model, base_url="http://127.0.0.1:8080/v1", api_key="local",
                 send_image=True, max_tokens=4000, thinking=False):
        self.model, self.base_url, self.api_key = model, base_url, api_key
        self.send_image, self.max_tokens = send_image, max_tokens
        # thinking=True asks an OpenAI-compatible server (vLLM/llama.cpp/SGLang) to turn on the
        # model's reasoning chat-template path via extra_body. NOTE: the dedicated
        # Qwen3-VL-*-Thinking checkpoint reasons WITHOUT this flag — the flag is for hybrid
        # Instruct models that gate thinking on enable_thinking. Sent with a no-flag retry in
        # case the server rejects the unknown field. Either way _extract_json strips the <think>.
        self.thinking = thinking
        self.last_raw = None
        self.last_reasoning = None
        self.context = None        # recalled memory armed by run_goal (see _inject)

    def decompose(self, goal, screen_png=None):
        return _extract_json(self._complete(f"GOAL: {goal}\n\nOutput the plan.", screen_png))

    def _complete(self, user_msg, screen_png=None):
        import openai
        client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key)
        content = [{"type": "text", "text": self._inject(user_msg)}]
        if screen_png and self.send_image:
            content.insert(0, {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(screen_png).decode()}})
        kw = dict(model=self.model, max_tokens=self.max_tokens,
                  messages=[{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": content}])
        # Set thinking on/off EXPLICITLY via the chat-template kwarg. The served model (e.g.
        # Qwen3.5) defaults thinking ON, so for a single next-action we must actively DISABLE it —
        # left on, the model burns hundreds-to-thousands of tokens reasoning per step (measured
        # ~600-6477 tok/call on the B580). Fall back to a plain call if the server rejects the field.
        try:
            r = client.chat.completions.create(
                extra_body={"chat_template_kwargs": {"enable_thinking": bool(self.thinking)}}, **kw)
        except Exception:
            r = client.chat.completions.create(**kw)   # server rejected the field -> plain call
        msg = r.choices[0].message
        self.last_raw = getattr(msg, "content", None) or ""   # raw reply for diagnosis
        # some servers split the chain-of-thought into a separate reasoning_content field
        self.last_reasoning = (getattr(msg, "reasoning_content", None)
                               or getattr(msg, "reasoning", None))
        return self.last_raw


class HFPlanner(LocalPlanner):
    """LocalPlanner pointed at Hugging Face's OpenAI-compatible inference.

    Same contract as the future B580 LocalPlanner — ONLY base_url/api_key differ, so a
    model validated here ports to local by changing base_url. Two ways to serve:
      - Serverless Inference Providers router (nothing to manage):
          base_url="https://router.huggingface.co/v1", model="<org>/<model>"
      - A dedicated Inference Endpoint you spun up:
          base_url="https://<name>.endpoints.huggingface.cloud/v1", model=<served model>
    api_key auto-resolves from huggingface_hub.get_token() (env or ~/.cache/huggingface/token)
    or HF_TOKEN/HUGGINGFACE_TOKEN/HUGGINGFACEHUB_API_TOKEN. send_image=True needs a VLM
    (e.g. Qwen/Qwen2.5-VL-7B-Instruct); use send_image=False for text-only planners."""

    def __init__(self, model, base_url="https://router.huggingface.co/v1",
                 api_key=None, send_image=True, max_tokens=4000, thinking=False):
        super().__init__(model=model, base_url=base_url,
                         api_key=api_key or self._hf_token(), send_image=send_image,
                         max_tokens=max_tokens, thinking=thinking)

    @staticmethod
    def _hf_token():
        try:
            from huggingface_hub import get_token
            t = get_token()
            if t:
                return t
        except Exception:
            pass
        for v in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
            if os.environ.get(v):
                return os.environ[v]
        return None


class RulePlanner(Planner):
    """Deterministic, dependency-free decomposition for the common task patterns.
    Not a general reasoner — it covers: launch an app + type text; compute an arithmetic
    expression in Calculator; and the combined multi-app task. Used to isolate executive
    reliability (fixed plan) and as an offline fallback when no model endpoint is set."""

    APP_ALIASES = {"notepad": "notepad", "calculator": "calc", "calc": "calc"}

    def decompose(self, goal, screen_png=None):
        g = goal.lower()
        steps = []
        # Notepad: "open notepad and type: <text>"
        m = re.search(r"(?:type|write)\s*:?\s*(.+?)(?:\.\s*then|;|\bthen\b|$)", goal, re.I)
        if "notepad" in g:
            steps += [{"op": "launch", "app": "notepad"}]
            if m:
                text = m.group(1).strip().strip('"').strip()
                # verify on a WHOLE-WORD prefix (not a mid-word chop like 'packag') so the
                # OCR substring match and the vision transcription line up cleanly.
                expect = text if len(text) <= 30 else text[:30].rsplit(" ", 1)[0]
                steps += [{"op": "type", "text": text},
                          {"op": "verify", "expect": expect}]
        # Calculator: "compute <expr>" / "<a> + <b>"
        cm = re.search(r"compute\s+(.+)$", goal, re.I) or re.search(
            r"([\d\.\s\+\-\*x×/]+=?)\s*$", goal)
        if "calc" in g or "calculator" in g or (cm and re.search(r"[\+\-\*/×x]", cm.group(1))):
            expr = (cm.group(1) if cm else "").strip().rstrip("=")
            expr = expr.replace("×", "*").replace("x", "*").replace(" ", "")
            if expr:
                try:
                    val = eval(expr, {"__builtins__": {}}, {})
                    steps += [{"op": "launch", "app": "calc"},
                              {"op": "type", "text": expr},
                              {"op": "tap", "key": "enter"},
                              {"op": "verify", "number==": str(val)}]
                except Exception:
                    pass
        steps += [{"op": "done"}]
        return steps


# ─────────────────────────────────────────── plan-time lint (preempt silent failures)
# Known ops -> required keys. `verify` is handled specially (needs one of expect/ask/number==).
_OP_FIELDS = {"launch": ("app",), "type": ("text",), "tap": ("key",), "key": ("combo",),
              "click": ("target",), "scroll": (), "sleep": (), "done": ()}
# ops that actually change the target's state. A plan with NONE of these cannot move a
# goal forward — a bare [done] / verify-only plan is a planning FAILURE, not success.
_ACTIONABLE = {"launch", "type", "tap", "key", "click"}
# verbs/words that mark a verify `expect` as a STATE CLAIM (which substring-match can't satisfy)
# rather than a literal on-screen string. Matched as whole words to avoid false positives.
_CLAIM_WORDS = ("is", "are", "was", "were", "now", "should", "has", "have", "set", "selected",
                "enabled", "disabled", "default", "shows", "showing", "appears", "open", "became")


def _looks_like_claim(s):
    """True if `s` reads like a sentence/state-claim rather than a literal label to find."""
    w = (s or "").strip().split()
    if len(w) >= 5:
        return True
    if len(w) >= 3 and any(x.lower().strip(".,?:;!") in _CLAIM_WORDS for x in w):
        return True
    return False


def validate_plan(plan):
    """Lint a plan BEFORE execution and return (clean_plan, issues).

    Preempts the silent-failure classes isolated this session, at plan time instead of as a
    wasted rollout:
      - verify whose `expect` is a state CLAIM (e.g. "Google Chrome is now the default web
        browser") -> auto-converted to a vision `ask` (substring-match could NEVER pass it —
        that exact bug killed every live default-browser run);
      - malformed / unknown / field-missing steps -> dropped with a note (was a mid-run
        "unknown op" or a KeyError);
      - click target that's a long descriptive clause -> WARNED (grounding mode still clicks,
        but a sentence hurts accuracy);
      - no trailing `done` -> appended.
    Conservative by design: short literal expects ("milk", "59", "Default apps") are left as
    substring verifies, so the keyboard benchmark is unaffected."""
    issues = []
    if not isinstance(plan, list):
        return [{"op": "done"}], ["plan was not a JSON list -> replaced with [done]"]
    clean = []
    for i, step in enumerate(plan):
        if not isinstance(step, dict) or "op" not in step:
            issues.append(f"step {i}: not an op dict -> dropped")
            continue
        op = step["op"]
        if op == "verify":
            if "number==" in step or "ask" in step:
                clean.append(step)
            elif "expect" in step:
                exp = str(step["expect"])
                if _looks_like_claim(exp):
                    q = exp if exp.rstrip().endswith("?") else f"Is this true on screen: {exp}?"
                    clean.append({"op": "verify", "ask": q})
                    issues.append(f"step {i}: verify.expect looked like a state claim "
                                  f"({exp!r}) -> converted to vision ask (substring can't match)")
                else:
                    clean.append(step)
            else:
                issues.append(f"step {i}: verify with no expect/ask/number== -> dropped")
            continue
        if op == "click":
            tgt = str(step.get("target", "")).strip()
            if not tgt:
                issues.append(f"step {i}: click with no target -> dropped")
                continue
            if len(tgt.split()) > 6:
                issues.append(f"step {i}: click target is a long clause ({tgt!r}); short labels "
                              f"ground better — keeping, but consider shortening")
            clean.append(step)
            continue
        if op not in _OP_FIELDS:
            issues.append(f"step {i}: unknown op {op!r} -> dropped")
            continue
        missing = [k for k in _OP_FIELDS[op] if k not in step]
        if missing:
            issues.append(f"step {i}: {op} missing {missing} -> dropped")
            continue
        clean.append(step)
    if not any(s.get("op") == "done" for s in clean):
        clean.append({"op": "done"})
        issues.append("no 'done' step -> appended")
    if (any(s.get("op") in _ACTIONABLE for s in clean)
            and not any(s.get("op") == "verify" for s in clean)):
        issues.append("plan has actions but no 'verify' step — the goal state will go "
                      "unchecked before 'done' (SYSTEM asks for a verify first)")
    return clean, issues


def plan_is_actionable(plan):
    """True if the plan has at least one op that changes the target's state. A plan of only
    done/sleep/verify cannot accomplish a non-trivial goal — used by run_goal to refuse to
    'succeed' on a no-op plan (the silent-success class)."""
    return any(isinstance(s, dict) and s.get("op") in _ACTIONABLE for s in plan)


def validate_step(step):
    """Lint ONE closed-loop step and return (clean_step | None, issues). Same rules as
    validate_plan, per-step: a claim-like verify.expect is converted to a vision ask; a malformed/
    unknown/field-missing step returns None (the loop rejects it and re-asks, rather than executing
    garbage or — the trap — silently treating it as 'done'). Reuses _OP_FIELDS / _looks_like_claim."""
    if not isinstance(step, dict) or "op" not in step:
        return None, [f"not an op dict ({step!r})"]
    op = step["op"]
    if op == "done":
        return {"op": "done"}, []
    if op == "verify":
        if "number==" in step or "ask" in step:
            return step, []
        if "expect" in step:
            exp = str(step["expect"])
            if _looks_like_claim(exp):
                q = exp if exp.rstrip().endswith("?") else f"Is this true on screen: {exp}?"
                return {"op": "verify", "ask": q}, ["verify.expect looked like a state claim -> ask"]
            return step, []
        return None, ["verify with no expect/ask/number=="]
    if op == "click":
        tgt = str(step.get("target", "")).strip()
        if not tgt:
            return None, ["click with no target"]
        return step, ([] if len(tgt.split()) <= 6 else ["click target is a long clause; short labels ground better"])
    if op not in _OP_FIELDS:
        return None, [f"unknown op {op!r}"]
    missing = [k for k in _OP_FIELDS[op] if k not in step]
    if missing:
        return None, [f"{op} missing {missing}"]
    return step, []


def summarize_result(result):
    """One compact, model-readable line explaining why an executive run failed — this is the
    per-attempt history that run_goal threads into replan. Prefers the executive's
    `failure_summary` (it carries the on-screen text the planner can't otherwise see); falls back
    to synthesizing from the step log so a summary is always available."""
    if not isinstance(result, dict):
        return "previous attempt failed (no result object)"
    status = result.get("status", "?")
    fs = result.get("failure_summary")
    if fs:
        return str(fs)
    if str(status).startswith("no-op"):
        return ("the plan had no real actions (only done/verify) and could not move the goal "
                "forward — produce a plan with concrete launch/type/tap/key/click steps")
    log = result.get("log", []) or []
    if not log:
        return f"failed ({status}) with no steps executed"
    last = log[-1]
    op = last.get("op")
    step = last.get("step", {}) or {}
    tgt = (step.get("target") or step.get("app") or step.get("text") or step.get("combo")
           or step.get("key") or step.get("ask") or step.get("expect") or step.get("number=="))
    desc = f"step {last.get('i')} ({op}{' ' + repr(tgt) if tgt else ''})"
    if last.get("error"):
        return f"{desc} raised {last['error']}"
    if op == "verify" and "got" in last:
        return f"{desc} failed: display read {last.get('got')!r}, expected {step.get('number==')!r}"
    if op == "click":
        return f"{desc} failed: the click did not change the screen (wrong target or none present)"
    return f"{desc} failed ({status})"


# ─────────────────────────────────────────── memory arming (RAG + hard-fact enforcement)
def _step_desc(step):
    """A short human/model-readable label for a step (events + closed-loop history)."""
    if not isinstance(step, dict):
        return str(step)
    op = step.get("op")
    d = (step.get("app") or step.get("target") or step.get("text") or step.get("combo")
         or step.get("key") or step.get("ask") or step.get("expect") or step.get("number==")
         or step.get("direction") or "")
    return (f"{op} {d}").strip() if d else str(op)


def _memory_block(directives, facts):
    """Compose planner.context: HARD CONSTRAINTS first, phrased as imperative directives at the TOP
    of the turn (so a blocking recalled fact is a rule the model reads first, not a soft 'fyi' it
    skims past), then the soft recalled facts. Either part may be empty."""
    parts = []
    if directives:
        parts.append(
            "HARD CONSTRAINTS for THIS machine — these OVERRIDE the general idioms below. You MUST "
            "obey them; a step that violates one is REJECTED before it runs, so plan AROUND them:\n"
            + "\n".join(f"- {d}" for d in directives))
    if facts:
        parts.append(
            "RELEVANT MEMORY (recalled facts about this machine/task — use them to plan, but still "
            "verify on screen):\n" + "\n".join(f"- {f}" for f in facts))
    return "\n\n".join(parts)


def _arm_memory(planner, executive, memory, goal, on_event=None):
    """Orchestrator-side RAG WITH hard-fact separation (the retrieval->utilization fix). Recalls for
    the goal, splits recalled facts into imperative DIRECTIVES (+ machine-enforceable GATES) and
    soft FACTS, arms planner.context (directives first) and the executive's constraint gates.
    Returns the gate count. FAIL-SOFT: any error -> best-effort soft block / no memory; a memory
    outage never breaks a run."""
    directives, facts, gates = [], [], []
    try:
        c = memory.recall_constraints(goal)
        directives = c.get("directives", []) or []
        facts = c.get("facts", []) or []
        gates = c.get("gates", []) or []
    except Exception:
        # older memory client without recall_constraints, or a recall error -> plain soft block
        try:
            planner.context = (memory.recall_block(goal) or None)
        except Exception:
            planner.context = None
        if executive is not None:
            try:
                executive.set_constraints([])
            except Exception:
                pass
        return 0
    planner.context = _memory_block(directives, facts) or None
    if executive is not None:
        try:
            executive.set_constraints(gates)
        except Exception:
            pass
    if on_event:
        n = len(directives) + len(facts)
        if n:
            on_event(f"memory: recalled {n} fact(s) — {len(directives)} hard constraint(s), "
                     f"{len(gates)} enforced gate(s)")
    return len(gates)


def _disarm_memory(planner, executive):
    """Clear recalled context + gates after a goal so nothing leaks into the next task."""
    try:
        planner.context = None
    except Exception:
        pass
    if executive is not None:
        try:
            executive.set_constraints([])
        except Exception:
            pass


# transient (retryable) planner-call failures — a router/network blip should not nuke a whole
# closed-loop run (one planner call PER STEP makes these far more likely than in run_goal).
_TRANSIENT_ERR = ("connection", "timeout", "timed out", "temporarily", "rate limit", "ratelimit",
                  "overloaded", "502", "503", "504", "reset by peer", "remotedisconnected",
                  "read timed out", "service unavailable")


def _is_transient(e):
    s = (type(e).__name__ + " " + str(e)).lower()
    return any(t in s for t in _TRANSIENT_ERR)


# ─────────────────────────────────────────── closed-loop orchestration
def run_goal(goal, planner, executive, max_replans=2, reset_first=True, tag="goal", on_event=None,
             memory=None, write_memory=False):
    """Decompose -> execute -> (on failure) re-plan from the live screen -> repeat.
    Returns the final result dict; attaches the plan(s) tried. on_event(msg) streams
    short progress strings (used by the Open WebUI server)."""
    def _ev(m):
        if on_event:
            on_event(m)

    def _log_planner(pdebug, run_dir=None):
        """Persist the RAW planner reply + parsed/validated plan beside the run frames, so a
        bad plan is diagnosable without a rerun (the raw reply used to be discarded — which is
        why goal_111417/plan.json could not say WHY the plan was a bare [done])."""
        d = run_dir or executive._make_run_dir(pdebug.get("tag", tag))
        if not d:
            return
        try:
            json.dump(pdebug, open(os.path.join(d, "planner.json"), "w"), indent=2)
        except Exception:
            pass

    def _plan_and_run(make_plan, run_tag):
        """Decompose/replan -> lint -> GUARD no-op plans -> execute.
        A plan with no state-changing op cannot accomplish a non-empty goal; executing it
        would hit the bare 'done' and FALSELY report success (the silent-success class). So
        refuse to run it and return a loud failure result that drives a re-plan instead."""
        raw_plan = make_plan()
        plan, issues = validate_plan(raw_plan)
        for m in issues:
            _ev("lint: " + m)
        pdebug = {"planner": type(planner).__name__, "tag": run_tag,
                  "raw": getattr(planner, "last_raw", None),
                  "reasoning": getattr(planner, "last_reasoning", None),
                  "parsed": raw_plan, "validated": plan, "issues": issues}
        if goal.strip() and not plan_is_actionable(plan):
            _ev("guard: planner returned a NO-OP plan (no actions) — refusing to run it "
                "(it would falsely report success); will re-plan if budget remains")
            _log_planner(pdebug)
            return plan, {"status": "no-op plan: planner produced no actions",
                          "elapsed": 0.0, "goal": goal, "steps": 0,
                          "log": [{"note": "no actionable op", "plan": plan}], "run_dir": None}
        _ev(f"plan ready: {len(plan)} steps; executing…")
        res = executive.run_plan(plan, goal=goal, run_tag=run_tag, on_event=on_event)
        _log_planner(pdebug, res.get("run_dir"))
        return plan, res

    # arm the planner with recalled memory for this goal (opt-in; orchestrator-side RAG). Hard
    # recalled facts become imperative directives at the TOP of the prompt AND executive gates —
    # so a recalled prohibition actually changes behavior, not just gets soft-injected and ignored.
    if memory is not None:
        _arm_memory(planner, executive, memory, goal, on_event)
    if reset_first:
        _ev("resetting desktop to a clean state…")
        executive.reset_clean()
    _ev("planning…")
    plan, result = _plan_and_run(lambda: planner.decompose(goal, executive.observe()), tag)
    plans = [plan]
    history = []        # summaries of attempts that already failed (oldest first) — replan memory
    attempts = 0
    while result["status"] != "done" and attempts < max_replans:
        attempts += 1
        history.append(summarize_result(result))   # remember WHY this attempt failed
        _ev(f"step failed ({result['status']}); re-planning (attempt {attempts})…")
        _ev("recall: " + history[-1])
        try:
            hist = list(history)
            plan, result = _plan_and_run(
                lambda: planner.replan(goal, result, executive.observe(), hist),
                f"{tag}_re{attempts}")
        except Exception as e:
            result["replan_error"] = repr(e)
            break
        plans.append(plan)
    _ev(f"finished: {result['status']}")
    result["plans"] = plans
    result["replans"] = attempts
    result["history"] = history
    # write-back (opt-in): on success, retain the working recipe so a future similar goal can recall
    # HOW it was done. Only on a real 'done' — never learn a broken sequence.
    if memory is not None and write_memory and result.get("status") == "done" and plans:
        try:
            if memory.retain_recipe(goal, plans[-1]):
                _ev("memory: retained the successful recipe for next time")
        except Exception:
            pass
    if memory is not None:
        _disarm_memory(planner, executive)   # don't leak recalled context/gates into the next task
    return result


# ─────────────────────────────────────────── per-step closed loop (the "different run_goal")
def run_goal_step(goal, planner, executive, max_steps=12, reset_first=True, tag="goalstep",
                  on_event=None, memory=None, write_memory=False, stuck_limit=3):
    """PER-STEP CLOSED LOOP — the alternative to run_goal's decompose-then-run-blind-then-replan.

    observe -> ask the planner for the SINGLE next action given the LIVE screen + goal + short
    history -> execute that ONE step -> observe -> repeat, until done / stuck / max_steps. Because
    the planner is re-asked from the current frame every turn, the agent reacts to real state — it
    Esc's the broken-shortcut dialog the turn it appears instead of running a stale plan into it.

    Same support as run_goal: opt-in memory (recall+write), per-step frames/log via the executive,
    and the HARD-CONSTRAINT path — recalled prohibitions are surfaced as imperative directives at
    the top of every turn (planner.context) AND enforced as executive gates, so a step that
    violates one is blocked and fed back instead of silently executed.

    Returns a result dict: {status, elapsed, goal, steps, history, trace, run_dir, loop}."""
    def _ev(m):
        if on_event:
            on_event(m)

    if memory is not None:
        _arm_memory(planner, executive, memory, goal, on_event)
    if reset_first:
        _ev("resetting desktop to a clean state…")
        executive.reset_clean()

    run_dir = executive._make_run_dir(tag) if getattr(executive, "capture", False) else None
    history = []          # "did X -> result" lines, fed back into the planner each turn
    trace = []            # steps actually executed (for write-back on success)
    status = "incomplete"
    t0 = time.time()
    consecutive_fail = 0
    rejected_done = False
    for i in range(max_steps):
        screen = executive.observe()
        # planner call with transient-error retry (a single router/network blip mid-run should not
        # abort the whole goal — the closed loop makes one such call per step).
        raw, perr = None, None
        for attempt in range(3):
            try:
                raw = planner.next_step(goal, screen, history)
                perr = None
                break
            except Exception as e:
                perr = e
                if attempt < 2 and _is_transient(e):
                    _ev(f"planner call failed ({type(e).__name__}: {str(e)[:50]}); "
                        f"retrying ({attempt + 1}/2)…")
                    time.sleep(2 * (attempt + 1))
                    continue
                break
        if perr is not None:
            status = "planner-error"
            history.append(f"planner error: {perr!r}")
            _ev(f"planner error: {perr!r}")
            break
        step, issues = validate_step(raw)
        for m in issues:
            _ev("lint: " + m)
        if step is None:
            consecutive_fail += 1
            why = issues[0] if issues else "not a valid op"
            history.append(f"proposed an INVALID action {raw!r} (rejected: {why}); choose a valid op")
            _ev(f"lint: rejected invalid action ({why})")
            if consecutive_fail >= stuck_limit:
                status = "stuck"
                break
            continue
        op = step.get("op")
        if op == "done":
            # refuse a premature 'done' before ANY action ran (the silent-success class); give the
            # planner ONE nudge to continue, then trust it (the goal may truly be pre-satisfied).
            if not trace and goal.strip() and not rejected_done:
                rejected_done = True
                history.append("you output 'done' but no action has been taken yet — if the goal is "
                               "truly already satisfied on screen keep 'done', otherwise take the "
                               "next concrete action")
                _ev("guard: ignored a premature 'done' (nothing done yet) — re-asking")
                continue
            status = "done"
            _ev("done")
            break
        _ev(f"next: {_step_desc(step)}")
        res = executive.run_step(step, i=i, t0=t0, run_dir=run_dir, on_event=on_event)
        trace.append(step)
        if res["status"] == "done":
            status = "done"
            break
        if str(res["status"]).startswith("failed"):
            consecutive_fail += 1
            history.append(res.get("failure_summary") or f"{_step_desc(step)} failed ({res['status']})")
            _ev("recall: " + history[-1])
            if consecutive_fail >= stuck_limit:
                status = "stuck"
                break
        else:   # ok
            consecutive_fail = 0
            history.append(f"{_step_desc(step)} -> ok")
    if status == "incomplete":
        status = "max-steps"
    _ev(f"finished: {status}")
    result = {"status": status, "elapsed": round(time.time() - t0, 1), "goal": goal,
              "steps": len(trace), "history": history, "trace": trace, "run_dir": run_dir,
              "loop": "per-step"}
    # write-back (opt-in): only on a real 'done' (never learn a broken sequence)
    if memory is not None and write_memory and status == "done" and trace:
        try:
            if memory.retain_recipe(goal, trace + [{"op": "done"}]):
                _ev("memory: retained the successful recipe for next time")
        except Exception:
            pass
    if memory is not None:
        _disarm_memory(planner, executive)
    return result
