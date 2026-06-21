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
  {"op":"verify","expect":"59"}          # confirm text is on screen (OCR/vision)
  {"op":"verify","number==":"59"}        # confirm the prominent number == value (calc display)
  {"op":"sleep","secs":1.0}
  {"op":"done"}                          # goal complete
Prefer keyboard ops; use click only when there is no keyboard path. Always end with a
verify of the goal state, then done.
"""
import os, re, json, base64

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
    "- Cell refs: account for the table's real position on screen (a leading spacer column "
    "and title rows above the header are common, so the data may start below row 1 / right "
    "of column A).\n\n"
    "PLAN SCHEMA" + PLAN_SCHEMA_DOC
)


def _extract_json(text):
    """Pull a JSON array out of a model reply, tolerating fences/prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else json.loads(text)


class Planner:
    def decompose(self, goal, screen_png=None):
        raise NotImplementedError

    def replan(self, goal, result, screen_png=None):
        """Default: given a failed run result + the current screen, ask for a fresh plan
        that recovers from where we are. Subclasses with a model override _complete()."""
        fail = result.get("status", "")
        log = json.dumps(result.get("log", [])[-6:], indent=0)[:1500]
        msg = (f"GOAL: {goal}\n\nThe previous plan FAILED ({fail}). Recent steps:\n{log}\n\n"
               "Look at the CURRENT screen and output a NEW plan (JSON array) that recovers "
               "from the current state and completes the goal. If a window is already open, "
               "do not relaunch it.")
        return _extract_json(self._complete(msg, screen_png))

    def _complete(self, user_msg, screen_png=None):
        raise NotImplementedError


class ClaudePlanner(Planner):
    """Anthropic API planner. Strong reasoning + recovery; sees the screenshot."""

    def __init__(self, model="claude-opus-4-8", api_key=None, max_tokens=4000):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tokens = max_tokens

    def decompose(self, goal, screen_png=None):
        return _extract_json(self._complete(f"GOAL: {goal}\n\nOutput the plan.", screen_png))

    def _complete(self, user_msg, screen_png=None):
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        content = [{"type": "text", "text": user_msg}]
        if screen_png:
            content.insert(0, {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(screen_png).decode()}})
        r = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=SYSTEM,
            messages=[{"role": "user", "content": content}])
        return r.content[0].text


class LocalPlanner(Planner):
    """All-local target: an OpenAI-compatible reasoning model (e.g. served on the B580).

    Deploy on the desktop B580 (Intel Arc, 12GB): serve a reasoning model with an
    OpenAI-compatible /v1 endpoint — llama.cpp `llama-server` (Vulkan/SYCL backend runs
    on Arc) or vLLM/IPEX — then point base_url here. Vision is optional: if the served
    model is text-only, pass send_image=False and the planner reasons from the goal +
    executive feedback alone (the executive's verify ops still ground truth on-screen)."""

    def __init__(self, model, base_url="http://127.0.0.1:8080/v1", api_key="local",
                 send_image=True, max_tokens=4000):
        self.model, self.base_url, self.api_key = model, base_url, api_key
        self.send_image, self.max_tokens = send_image, max_tokens

    def decompose(self, goal, screen_png=None):
        return _extract_json(self._complete(f"GOAL: {goal}\n\nOutput the plan.", screen_png))

    def _complete(self, user_msg, screen_png=None):
        import openai
        client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key)
        content = [{"type": "text", "text": user_msg}]
        if screen_png and self.send_image:
            content.insert(0, {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(screen_png).decode()}})
        r = client.chat.completions.create(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": content}])
        return r.choices[0].message.content


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
                 api_key=None, send_image=True, max_tokens=4000):
        super().__init__(model=model, base_url=base_url,
                         api_key=api_key or self._hf_token(), send_image=send_image,
                         max_tokens=max_tokens)

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
                steps += [{"op": "type", "text": text},
                          {"op": "verify", "expect": text[:20]}]
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


# ─────────────────────────────────────────── closed-loop orchestration
def run_goal(goal, planner, executive, max_replans=2, reset_first=True, tag="goal", on_event=None):
    """Decompose -> execute -> (on failure) re-plan from the live screen -> repeat.
    Returns the final result dict; attaches the plan(s) tried. on_event(msg) streams
    short progress strings (used by the Open WebUI server)."""
    def _ev(m):
        if on_event:
            on_event(m)
    if reset_first:
        _ev("resetting desktop to a clean state…")
        executive.reset_clean()
    _ev("planning…")
    plan = planner.decompose(goal, executive.observe())
    plans = [plan]
    _ev(f"plan ready: {len(plan)} steps; executing…")
    result = executive.run_plan(plan, goal=goal, run_tag=tag, on_event=on_event)
    attempts = 0
    while result["status"] != "done" and attempts < max_replans:
        attempts += 1
        _ev(f"step failed ({result['status']}); re-planning (attempt {attempts})…")
        try:
            plan = planner.replan(goal, result, executive.observe())
        except Exception as e:
            result["replan_error"] = repr(e)
            break
        plans.append(plan)
        result = executive.run_plan(plan, goal=goal, run_tag=f"{tag}_re{attempts}", on_event=on_event)
    _ev(f"finished: {result['status']}")
    result["plans"] = plans
    result["replans"] = attempts
    return result
