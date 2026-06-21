"""
excel_practice2.py — CORRECTED column mapping. Column A is a spacer; the table is B:J.
  B Date | C Sales Rep | D Shift | E Cost | F Selling | G Profit | H Profit% | I Commission | J Weekday
Header row 4, data row 5+. Fill the clean columns:
  G Profit   = Selling(F) - Cost(E)
  H Profit % = Profit / Cost * 100        (markup; flagged)
  J Weekday  = weekday name of the Date (B), robust to date-stored-as-text
IF-guarded, overshoot to row 300, Go To + Ctrl+Enter. Commission (I) left for the
Reference-Data step. Focus-gate + Esc-only modal dismissal (reboot lesson).
"""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier

TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\practice2_{TS}"; os.makedirs(OUT, exist_ok=True)
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
    r4.combo("ctrl+g"); time.sleep(1.1)
    r4.type(rng); time.sleep(0.4)
    r4.key("enter"); time.sleep(0.8)
    r4.type(formula); time.sleep(0.5)
    r4.combo("ctrl+enter"); time.sleep(0.9)
    log(f"filled {rng}  ->  {formula}")

try:
    ex.dismiss_modal(1)
    snap(env, "00_before")
    if not ex.confirm("Is a Microsoft Excel spreadsheet with a data table the active window?"):
        log("ABORT: Excel not confirmed active"); snap(env, "00b_nofocus")
    else:
        r4.combo("ctrl+home"); time.sleep(0.8)
        fill("G5:G300", '=IF(E5="","",F5-E5)')                 # Profit  = Selling - Cost
        fill("H5:H300", '=IF(E5="","",(F5-E5)/E5*100)')         # Profit% = markup
        fill("J5:J300", '=IF(B5="","",TEXT(IF(ISNUMBER(B5),B5,DATEVALUE(B5)),"dddd"))')  # Weekday
        r4.combo("ctrl+home"); time.sleep(0.7)
        snap(env, "01_filled")
        log("verify: " + str(ex.verifier._vision(env.observe()["screenshot"],
            "In this Excel sheet, look at the first data row (row 5). What are the values in "
            "the Profit, Profit %, and Weekday columns? Are there any #VALUE! errors? Answer briefly.")))
    log("DONE")
except Exception as e:
    log(f"ERROR {e!r}")
finally:
    try:
        env.close(); log("rig closed")
    except Exception as ex2:
        log(f"close err {ex2!r}")
