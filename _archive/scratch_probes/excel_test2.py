"""
excel_test2.py — Excel task, hardened against the Windows-Update-modal incident.

Safety changes vs v1:
  - Dismiss stray modals with ESC (never Enter — Enter activated the update dialog's
    default 'Restart now' last time and rebooted the box).
  - Vision GATE before typing: only enter data once the verifier confirms an Excel grid
    is actually open + focused. No keystrokes into an unconfirmed window.
Runs as a background process (boots, acts, snaps each step, shuts down) so no single
call hits the ~60s tool timeout.
"""
import sys, os, re, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier

TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\excel2_{TS}"
os.makedirs(OUT, exist_ok=True)
LOG = []
def log(m):
    print(m, flush=True); LOG.append(f"{time.strftime('%H:%M:%S')} {m}")
    json.dump(LOG, open(os.path.join(OUT, "log.json"), "w"), indent=1)

log(f"booting -> {OUT}")
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920, 1080))
ex = Executive(env, ag, verifier=Verifier("qwen2.5vl:7b"), log_dir=OUT)
r4 = env.r4

def snap(name):
    open(os.path.join(OUT, name + ".png"), "wb").write(env.observe()["screenshot"])
    log(f"snap {name}")
def ask(q):
    a = (ex.verifier._vision(env.observe()["screenshot"], q) or "")
    log(f"ask {q[:48]!r} -> {a[:70]!r}"); return a.lower()
def ground(instr):
    ag.reset(); _t, acts = ag.predict(instr, {"screenshot": env.observe()["screenshot"]})
    for a in acts:
        m = re.search(r"\((\d+),\s*(\d+)\)", a)
        if m:
            xy = (int(m.group(1)), int(m.group(2))); log(f"ground {instr[:40]!r} -> {xy}"); return xy
    log(f"ground {instr[:40]!r} -> NONE"); return None
def cell(text, after):
    r4.type(text); time.sleep(0.35); r4.key(after); time.sleep(0.35)

try:
    snap("00_initial")
    # 1) SAFE dismiss of any stray modal (Esc only; never Enter)
    for _ in range(2):
        r4.key("esc"); time.sleep(0.6)
    # 2) clear leftover windows (vision-gated; safe in a background script)
    log("reset_clean: " + str(ex.reset_clean(max_close=8)))
    snap("01_clean")
    # 3) launch Excel
    r4.combo("win+r"); time.sleep(1.3); r4.type("excel"); time.sleep(0.4)
    r4.key("enter"); time.sleep(10.0)
    snap("02_launched")
    # 4) dismiss any blocking system dialog (update/sign-in) with Esc, re-check
    for _ in range(3):
        if "yes" in ask("Is a Windows system dialog or popup (Windows Update, sign-in, "
                        "notification) blocking the screen, not Excel? Answer yes or no."):
            r4.key("esc"); time.sleep(1.2)
        else:
            break
    # 5) start screen -> Blank workbook
    if "yes" in ask("Does the screen show the Excel START screen with template thumbnails "
                    "(e.g. 'Blank workbook')? Answer yes or no."):
        xy = ground("click the 'Blank workbook' template thumbnail near the top-left")
        if xy:
            r4.move(*xy); r4.click(); time.sleep(3.5)
    snap("03_blank")
    # 6) SAFETY GATE: only type if an Excel grid is confirmed open + focused
    if "yes" not in ask("Is an empty Excel spreadsheet grid (lettered columns, numbered "
                        "rows of cells) open and active? Answer yes or no."):
        log("ABORT: Excel grid not confirmed — will NOT type (safety gate)")
        snap("03b_nogrid")
    else:
        cell("Item", "tab");   cell("Cost", "enter")
        cell("Coffee", "tab"); cell("5", "enter")
        cell("Lunch", "tab");  cell("12", "enter")
        cell("Total", "tab");  cell("=SUM(B2:B3)", "enter")
        time.sleep(1.0)
        snap("04_entered")
        log("total 17 on screen? " + ask("Does a cell show the number 17 (a column total)? yes or no."))
        xy = ground("click the cell showing the total 17 at the bottom of the Cost column")
        if xy:
            r4.move(*xy); r4.click(); time.sleep(1.0)
        snap("05_final")
    log("DONE")
except Exception as e:
    log(f"ERROR {e!r}")
finally:
    try:
        env.close(); log("rig closed")
    except Exception as ex2:
        log(f"close err {ex2!r}")
