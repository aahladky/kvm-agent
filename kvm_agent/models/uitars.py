"""
uitars_agent.py — UI-TARS-1.5-7B adapter for the Pico rig.

Drop-in for the same contract operate.py / run_probe.py already use with EvoCUAAgent:
    agent.reset()
    text, actions = agent.predict(instruction, obs)   # obs["screenshot"] = PNG bytes
    agent.last_answer            # finished(content=...) text, else None
`actions` is a list of strings: pyautogui code (executed by pico_env's shim) OR the
control tokens "WAIT" / "DONE" / "FAIL" that pico_env.step understands.

WHY THIS EXISTS / KEY DECISIONS (verified offline 2026-06-19, see tests/test_uitars_adapter.py):

1. COORDINATES. UI-TARS-1.5-7B (Qwen2.5-VL) emits ABSOLUTE pixel coordinates on the
   *smart-resized* image (factor 28). The `ui-tars` package's
   `parse_action_to_structure_output(..., model_type="qwen25vl")` normalizes those to
   [0,1] fractions (it divides by the smart_resize dims). We then map fraction -> real
   pixel ourselves: x = frac_x * W, y = frac_y * H (W,H = the real capture frame dims).
   NOTE: the package's `parsing_response_to_pyautogui_code` defaults to scale_factor=1000,
   which is WRONG for this normalized path (yields sub-pixel 0.96 instead of 960). We do
   the final px math here instead, so that footgun can't bite.

2. NO `import` LINES. `parsing_response_to_pyautogui_code` prepends `import pyautogui` /
   `import time` / `import pyperclip` and a docstring. pico_env execs action strings in a
   shim namespace ({"pyautogui": PicoPyAutoGUI, "time": time}); an `import pyautogui` there
   would rebind to the REAL module (controlling the wrong machine) or crash. So we generate
   bare action calls only — no imports, no docstring.

3. NO CLIPBOARD TYPING. The package's `type` uses pyperclip + ctrl+V (input_swap=True).
   The Pico target has no pyperclip/clipboard guarantee, and PicoPyAutoGUI implements
   typewrite -> r4.type. So we emit `pyautogui.typewrite(...)` (per-char over HID), and a
   trailing Enter only if the model's content ended with a newline (UI-TARS submit convention).

We use the `ui-tars` package ONLY for DSL parsing (robust across click/type/hotkey/scroll/
drag/finished/wait and its escaping); all execution-string generation + pixel math is local
and unit-tested.
"""
import os
import re
import time
import ast
import base64
import logging
from io import BytesIO
from typing import Dict, List, Tuple, Optional

from PIL import Image

from ui_tars.action_parser import parse_action_to_structure_output
from ui_tars.prompt import COMPUTER_USE_DOUBAO, GROUNDING_DOUBAO

logger = logging.getLogger("desktopenv.uitars")

# Map UI-TARS / browser key names -> names r4_client.norm_key understands (mirrors the
# arrow-key remap the ui-tars package does for pyautogui).
_KEY_REMAP = {
    "arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down",
    "return": "enter", "escape": "esc", "delete": "del", "control": "ctrl",
}


class UITARSAgent:
    """UI-TARS-1.5-7B over an OpenAI-compatible endpoint (Ollama), Pico-rig output."""

    def __init__(
        self,
        model: str = "uitars-q4",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        top_p: float = 0.9,
        max_history_turns: int = 4,
        screen_size: Tuple[int, int] = (1920, 1080),
        language: str = "English",
        # smart_resize budget the SERVER uses for this model; MUST match Ollama's qwen2.5-vl
        # image processing or grounding is biased. These are the values the ollama load log
        # prints as image_min_pixels / image_max_pixels for the UI-TARS-1.5-7B mmproj.
        # At 1920x1080/1088 any budget gives the same dims (1932x1092) — verified, which is why
        # calibration passed — but a mismatch diverges once the capture leaves the [min,max] band
        # (e.g. 1440p). If you swap the mmproj, re-read those two numbers from the load log.
        min_pixels: int = 802816,
        max_pixels: int = 3211264,
        timeout: float = 180.0,
        grounding: bool = False,
        **kwargs,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.max_history_turns = max_history_turns
        self.screen_size = screen_size
        self.language = language
        self.min_pixels = min_pixels
        self.max_pixels = max_pixels
        self.timeout = timeout
        self.grounding = grounding

        # state (mirrors EvoCUAAgent so operate.py/run_probe.py treat them identically)
        self.screenshots: List[str] = []   # base64 PNG of prior turns
        self.responses: List[str] = []     # raw model text of prior turns
        self.actions: List = []
        self.last_answer: Optional[str] = None

    # ------------------------------------------------------------------ lifecycle
    def reset(self, _logger=None, vm_ip=None):
        global logger
        if _logger:
            logger = _logger
        self.screenshots = []
        self.responses = []
        self.actions = []
        self.last_answer = None

    # ------------------------------------------------------------------ main entry
    def predict(self, instruction: str, obs: Dict) -> Tuple[str, List[str]]:
        self.last_answer = None
        png = obs["screenshot"]
        try:
            W, H = Image.open(BytesIO(png)).size
        except Exception as e:
            logger.warning(f"could not read screenshot size, using screen_size: {e}")
            W, H = self.screen_size
        cur_b64 = base64.b64encode(png).decode()

        messages = self._build_messages(instruction, cur_b64)
        text = self._call_llm(messages) or ""

        # store this turn; history is replayed as a coordinate-free TEXT summary of responses
        # (see _build_messages) — NOT as verbatim coord-bearing turns, which caused mimicry.
        self.screenshots.append(cur_b64)
        self.responses.append(text)

        actions = self._to_actions(text, W, H)
        self.actions.append(actions)
        return text, actions

    # ------------------------------------------------------------------ messages
    def _build_messages(self, instruction: str, current_b64: str) -> List[Dict]:
        """History as a coordinate-free TEXT action-summary + ONLY the current screenshot.

        WHY (proven offline 2026-06-20): replaying prior turns as assistant messages that
        contain raw `click(start_box='(x,y)')` makes UI-TARS COPY a coordinate sitting in
        context instead of grounding the current target — it emitted the identical (251,553)
        for both "2" and "7". The mimicry channel is the copyable coords in replayed actions
        (same class as the EvoCUA flail). A/B on the captured frames:
          baseline (coords in history)         -> clicks "2"  (copies history)
          text summary (this) + current image  -> clicks "7"  (grounds correctly) + valid coords
        Conveying state as a coordinate-free summary keeps task state but leaves nothing to
        copy, so the model re-grounds each step. Bonus: 1 image/request, not N+1 -> ~3s/step.
        """
        if self.grounding:
            # GROUNDING mode: the action space is click-ONLY (no finished/scroll/wait), so the
            # model cannot prematurely terminate or scroll when the target is already visible —
            # the failure isolated 2026-06-21 where COMPUTER_USE made UI-TARS emit finished()
            # instead of clicking a clearly-visible 'Google Chrome' chooser item. Single-shot;
            # no history (the executive grounds one target per fresh frame).
            prompt = GROUNDING_DOUBAO.format(instruction=instruction)
        else:
            prompt = COMPUTER_USE_DOUBAO.format(language=self.language, instruction=instruction)
            summary = self._history_summary()
            if summary:
                prompt += "\n\nPrevious actions already performed (most recent last):\n" + summary
        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{current_b64}"}}]},
        ]

    def _history_summary(self) -> str:
        """Coordinate-free running log of prior actions (state for the model, nothing to copy)."""
        resps = self.responses[-20:]  # text is cheap; keep plenty of state, no image-token cost
        return "\n".join(f"{i}. {self._summarize_action(r)}" for i, r in enumerate(resps, 1))

    @staticmethod
    def _summarize_action(resp: str) -> str:
        act = resp.split("Action:")[-1] if "Action:" in resp else resp
        mt = re.search(r"type\(content='(.*?)'\)", act, re.S)
        if mt:
            return f'typed "{mt.group(1)}"'
        mk = re.search(r"hotkey\(key='([^']*)'\)", act)
        if mk:
            return f"pressed {mk.group(1)}"
        if "finished(" in act:
            return "marked the task finished"
        if "wait(" in act:
            return "waited"
        if re.search(r"(left_double|double_click)\(", act):
            verb = "double-clicked"
        elif re.search(r"right_single\(", act):
            verb = "right-clicked"
        elif re.search(r"(click|left_single|drag|select|hover)\(", act):
            verb = "clicked"
        else:
            return "performed an action"
        tgt = UITARSAgent._target_from_thought(resp.split("Action:")[0])
        return f"{verb} {tgt}" if tgt else f"{verb} an element"

    @staticmethod
    def _target_from_thought(th: str):
        """Best-effort BARE target label from the model's own Thought.

        Bare labels ("clicked 4") are what worked in the offline A/B; decorated forms
        ('clicked the "4"') deterministically broke grounding (model went far off-screen).
        So: no quotes, no "the ... button" wrapper, and operator words -> symbols.
        """
        sym = {"plus": "+", "add": "+", "minus": "-", "subtract": "-", "equals": "=",
               "times": "*", "multiply": "*", "divide": "/"}
        for pat in (r'["\']([A-Za-z0-9+\-=*/.]{1,24})["\']\s*(?:button|key|icon|tab|field|menu|item)',
                    r'\bthe ([A-Za-z0-9][\w +\-=.\']{0,24}?)\s+(?:button|icon|key|tab|field|menu|item)'):
            mm = re.search(pat, th)
            if mm:
                return mm.group(1).strip()
        mm = re.search(r'\b(plus|add|minus|subtract|equals|times|multiply|divide)\b', th, re.I)
        return sym[mm.group(1).lower()] if mm else None

    def _call_llm(self, messages: List[Dict]) -> str:
        import openai  # lazy: keeps the adapter importable (and unit-testable) without the dep
        from kvm_agent.config import CFG
        base_url = os.environ.get("OPENAI_BASE_URL", CFG.openai_base)
        api_key = os.environ.get("OPENAI_API_KEY", CFG.openai_key)
        client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=self.timeout)
        last_err = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=self.model, messages=messages,
                    max_tokens=self.max_tokens, temperature=self.temperature,
                    top_p=self.top_p,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # transient endpoint hiccups
                last_err = e
                logger.warning(f"call_llm attempt {attempt+1} failed: {e}")
                time.sleep(1.5 * (attempt + 1))
        logger.error(f"call_llm gave up: {last_err}")
        return ""

    # ------------------------------------------------------------------ parsing -> actions
    def _to_actions(self, text: str, W: int, H: int) -> List[str]:
        """Parse UI-TARS DSL -> list of pico_env action strings / control tokens.
        Returns [] on parse failure (operate.py counts that toward the empty-streak guard)."""
        if not text or "Action:" not in text:
            return []
        try:
            parsed = parse_action_to_structure_output(
                text, factor=1000, origin_resized_height=H, origin_resized_width=W,
                model_type="qwen25vl", min_pixels=self.min_pixels, max_pixels=self.max_pixels,
            )
        except Exception as e:
            logger.warning(f"parse failed: {e} :: {text[:120]!r}")
            return []

        actions: List[str] = []
        for a in parsed:
            try:
                s = self._action_to_pico(a, W, H)
            except Exception as e:   # one malformed action must never crash the whole run
                logger.warning(f"action map failed ({e}) :: {a}")
                s = None
            if s:
                actions.append(s)
        return actions

    def _px(self, box_str, W: int, H: int):
        """Map a parsed start_box ('[fx,fy,fx,fy]' of [0,1] fractions) to real px. None if missing/bad."""
        if not box_str:
            return None
        try:
            b = ast.literal_eval(box_str) if isinstance(box_str, str) else box_str
            if len(b) == 4:
                fx, fy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
            else:
                fx, fy = b[0], b[1]
            return int(round(fx * W)), int(round(fy * H))
        except Exception:
            return None

    def _action_to_pico(self, a: Dict, W: int, H: int) -> Optional[str]:
        t = a.get("action_type")
        ins = a.get("action_inputs", {}) or {}

        if t in ("click", "left_single", "left_double", "right_single", "hover"):
            xy = self._px(ins.get("start_box"), W, H)
            if not xy:
                return None
            x, y = xy
            if t == "left_double":
                return f"pyautogui.doubleClick({x}, {y})"
            if t == "right_single":
                return f"pyautogui.rightClick({x}, {y})"
            if t == "hover":
                return f"pyautogui.moveTo({x}, {y})"
            return f"pyautogui.click({x}, {y})"

        if t == "type":
            content = ins.get("content", "")
            submit = content.endswith("\n") or content.endswith("\\n")
            body = content.rstrip("\n").replace("\\n", "\n")
            code = f"pyautogui.typewrite({body!r})"
            if submit:
                code += "\npyautogui.press('enter')"
            return code

        if t == "hotkey":
            raw = ins.get("key") or ins.get("hotkey") or ""
            keys = [_KEY_REMAP.get(k, k) for k in raw.split()]
            keys = [(" " if k == "space" else k) for k in keys]
            if not keys:
                return None
            return "pyautogui.hotkey(" + ", ".join(repr(k) for k in keys) + ")"

        if t in ("press", "keydown"):
            k = ins.get("key") or ins.get("press") or ""
            k = _KEY_REMAP.get(k, k)
            return f"pyautogui.press({k!r})" if k else None

        if t == "scroll":
            direction = (ins.get("direction") or "").lower()
            n = 5 if "up" in direction else (-5 if "down" in direction else 0)
            if n == 0:
                return None
            if ins.get("start_box"):
                x, y = self._px(ins["start_box"], W, H)
                return f"pyautogui.scroll({n}, x={x}, y={y})"
            return f"pyautogui.scroll({n})"

        if t in ("drag", "select"):
            sx, sy = self._px(ins["start_box"], W, H)
            ex, ey = self._px(ins["end_box"], W, H)
            return f"pyautogui.moveTo({sx}, {sy})\npyautogui.dragTo({ex}, {ey}, duration=1.0)"

        if t == "wait":
            return "WAIT"
        if t == "finished":
            self.last_answer = ins.get("content") or None
            return "DONE"
        if t in ("call_user", "fail"):
            self.last_answer = ins.get("content") or None
            return "FAIL"

        logger.warning(f"unmapped UI-TARS action_type: {t!r}")
        return None
