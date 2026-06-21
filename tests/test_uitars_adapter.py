"""
Offline tests for the UI-TARS adapter — no rig, no Ollama (the hardware-free probe
methodology from FINDINGS). Verifies DSL parse -> pico_env action strings:
  * coordinate math against HAND-COMPUTED pixels (center / corners / the search-box case),
  * type / hotkey / scroll / drag / press generation,
  * control-token mapping (finished->DONE+answer, wait->WAIT),
  * exec-safety invariant: no `import` lines, only PicoPyAutoGUI-supported calls,
  * graceful empty-parse (-> [], which operate.py's empty-streak guard expects).

Run:  python3 tests/test_uitars_adapter.py
"""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uitars_agent import UITARSAgent

W, H = 1920, 1088                 # our real capture; smart_resize(28) -> 1932 x 1092
A = UITARSAgent(model="t", max_history_turns=0)

# PicoPyAutoGUI (pico_env) supports exactly these mouse/keyboard methods:
ALLOWED = {"click", "doubleClick", "rightClick", "moveTo", "dragTo",
           "typewrite", "press", "hotkey", "scroll"}
passed = failed = 0

def act(resp):
    return A._to_actions(f"Thought: do it\nAction: {resp}", W, H)

def check(name, cond, got=None):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {name}")
    else:
        failed += 1; print(f"  FAIL  {name}   got={got!r}")

def only_xy(s):
    m = re.search(r"\((\d+),\s*(\d+)\)", s)
    return (int(m.group(1)), int(m.group(2))) if m else None

# ---- coordinate grounding (the whole point) ----
check("click center -> (960,544)", act("click(point='<point>966 546</point>')") == ["pyautogui.click(960, 544)"],
      act("click(point='<point>966 546</point>')"))
check("click TL -> (0,0)", act("click(point='<point>0 0</point>')") == ["pyautogui.click(0, 0)"],
      act("click(point='<point>0 0</point>')"))
check("click BR -> (1920,1088)", act("click(point='<point>1932 1092</point>')") == ["pyautogui.click(1920, 1088)"],
      act("click(point='<point>1932 1092</point>')"))
# the EvoCUA-flail search box: model emits resized-px ~ (177,1070) -> real ~ (176,1066)
check("click searchbox -> (176,1066)", act("click(point='<point>177 1070</point>')") == ["pyautogui.click(176, 1066)"],
      act("click(point='<point>177 1070</point>')"))

# ---- click variants ----
check("left_double", act("left_double(point='<point>966 546</point>')") == ["pyautogui.doubleClick(960, 544)"])
check("right_single", act("right_single(point='<point>966 546</point>')") == ["pyautogui.rightClick(960, 544)"])

# ---- type ----
check("type plain", act("type(content='milk, eggs, and bread')") == ["pyautogui.typewrite('milk, eggs, and bread')"],
      act("type(content='milk, eggs, and bread')"))
sub = act("type(content='hello\\n')")
check("type submit appends Enter", sub == ["pyautogui.typewrite('hello')\npyautogui.press('enter')"], sub)

# ---- hotkey ----
check("hotkey ctrl c", act("hotkey(key='ctrl c')") == ["pyautogui.hotkey('ctrl', 'c')"], act("hotkey(key='ctrl c')"))
check("hotkey ctrl w (close Notepad)", act("hotkey(key='ctrl w')") == ["pyautogui.hotkey('ctrl', 'w')"])

# ---- scroll ----
sc = act("scroll(point='<point>500 500</point>', direction='down')")
check("scroll down -> negative + xy", sc == ["pyautogui.scroll(-5, x=497, y=498)"], sc)

# ---- drag ----
dg = act("drag(start_point='<point>100 100</point>', end_point='<point>200 200</point>')")
check("drag -> moveTo+dragTo", dg == ["pyautogui.moveTo(99, 100)\npyautogui.dragTo(199, 199, duration=1.0)"], dg)

# ---- control tokens ----
A.last_answer = None
fin = act("finished(content='61')")
check("finished -> DONE", fin == ["DONE"], fin)
check("finished sets last_answer", A.last_answer == "61", A.last_answer)
check("wait -> WAIT", act("wait()") == ["WAIT"], act("wait()"))

# ---- robustness ----
check("no Action line -> []", A._to_actions("Thought: hmm, let me look around.", W, H) == [])
check("garbage -> []", A._to_actions("totally not a tool call", W, H) == [])

# ---- exec-safety invariants over every generated action ----
samples = [
    "click(point='<point>966 546</point>')", "left_double(point='<point>10 10</point>')",
    "right_single(point='<point>10 10</point>')", "type(content='abc\\n')",
    "hotkey(key='ctrl c')", "scroll(point='<point>5 5</point>', direction='up')",
    "drag(start_point='<point>1 1</point>', end_point='<point>9 9</point>')",
]
all_strs = [s for r in samples for s in act(r)]
check("no 'import' in any action (exec-shim safe)", all(" import " not in s and not s.startswith("import") for s in all_strs))
fns = {m for s in all_strs for m in re.findall(r"pyautogui\.(\w+)\(", s)}
check(f"only PicoPyAutoGUI-supported calls {sorted(fns)}", fns <= ALLOWED, sorted(fns))
check("no leftover docstring/observation noise", all("Observation" not in s and "'''" not in s for s in all_strs))

print(f"\n==== {passed} passed, {failed} failed ====")
sys.exit(1 if failed else 0)
