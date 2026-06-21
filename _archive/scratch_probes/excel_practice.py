"""
excel_practice.py — attempt the formula columns of the 'Data for Practice' sheet.

Fills the dependency-free columns (the clean part of the 5 tasks):
  F Profit   = Selling - Cost
  G Profit % = Profit / Cost * 100        (markup convention; flagged to the user)
  I Weekday  = TEXT(Date, "dddd")
Header row 4, data row 5+. IF-guarded + overshoot to row 300 (harmless on blank rows),
so the exact last row doesn't matter. Range select via Go To (Ctrl+G), commit with
Ctrl+Enter (fills the whole selection with relative refs) — fully keyboard.

Safety (Windows-Update-reboot lesson): dismiss stray popups with Esc, and a vision
FOCUS GATE — only type if Excel is confirmed the active window.
"""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier

TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\practice_{TS}"; os.makedirs(OUT, exist_ok=True)
LOG = []
def log(m):
    print(m, flush=True); LOG.append(f"{time.strftime('%H:%M:%S')} {m}")
    json.dump(LOG, open(os.path.join(OUT, "log.json"), "w"), indent=1)
def snap(env, n): open(os.path.join(OUT, n + ".png"), "wb").write(env.observe()["screenshot"]); log("snap " + n)

env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920, 1080))
ex = Executive(env, ag, verifier=Verifier("qwen2.5vl:7b"))
r4 = env.r4

def fill(rng, formula):
    r4.combo("ctrl+g"); time.sleep(1.1)          # Go To dialog (Reference field focused)
    r4.type(rng); time.sleep(0.4)
    r4.key("enter"); time.sleep(0.8)              # select the range
    r4.type(formula); time.sleep(0.5)
    r4.combo("ctrl+enter"); time.sleep(0.9)       # fill the whole selection (relative refs)
    log(f"filled {rng}  ->  {formula}")

try:
    ex.dismiss_modal(1)
    snap(env, "00_before")
    foc = ex.confirm("Is a Microsoft Excel spreadsheet with a data table the active window "
                     "(not the desktop or another app)?")
    log(f"excel focused gate: {foc}")
    if not foc:
        log("ABORT: Excel not confirmed as active window (safety gate)")
        snap(env, "00b_nofocus")
    else:
        r4.combo("ctrl+home"); time.sleep(0.8)
        fill("F5:F300", '=IF(D5="","",E5-D5)')
        fill("G5:G300", '=IF(D5="","",(E5-D5)/D5*100)')
        fill("I5:I300", '=IF(A5="","",TEXT(A5,"dddd"))')
        r4.combo("ctrl+home"); time.sleep(0.7)
        snap(env, "01_filled")
        log("verify: " + str(ex.verifier._vision(env.observe()["screenshot"],
            "In this Excel sheet, do the columns titled Profit, Profit %, and Weekday now "
            "contain a value on every data row? Answer yes or no and name what you see in "
            "the first data row.")))
    log("DONE")
except Exception as e:
    log(f"ERROR {e!r}")
finally:
    try:
        env.close(); log("rig closed")
    except Exception as ex2:
        log(f"close err {ex2!r}")
