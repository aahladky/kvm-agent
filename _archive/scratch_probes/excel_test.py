"""
excel_test.py — real-world Excel task on the rig, run as ONE autonomous background
process (boots, acts, snaps a frame each step, shuts down cleanly). Driven this way
because long single calls over the interactive REPL hit the ~60s tool timeout and drop
the session — a background script + file polling is the robust pattern.

Task: open Excel -> blank workbook -> type a small Item/Cost budget with a =SUM total
-> grounding-click the total cell -> verify by saved frames. Keyboard for data entry
(Tab across, Enter down); UI-TARS stateless grounding for the 'Blank workbook' click and
the total-cell click (the genuine visual-grounding parts).
"""
import sys, os, re, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent

TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\excel_{TS}"
os.makedirs(OUT, exist_ok=True)
LOG = []
def log(m):
    print(m, flush=True); LOG.append(f"{time.strftime('%H:%M:%S')} {m}")
    json.dump(LOG, open(os.path.join(OUT, "log.json"), "w"), indent=1)

log(f"booting rig -> {OUT}")
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920, 1080))
r4 = env.r4

def snap(name):
    open(os.path.join(OUT, name + ".png"), "wb").write(env.observe()["screenshot"])
    log(f"snap {name}")

def ground(instr):
    ag.reset()
    _t, actions = ag.predict(instr, {"screenshot": env.observe()["screenshot"]})
    for a in actions:
        m = re.search(r"\((\d+),\s*(\d+)\)", a)
        if m:
            xy = (int(m.group(1)), int(m.group(2))); log(f"ground {instr!r} -> {xy}"); return xy
    log(f"ground {instr!r} -> NONE ({actions})"); return None

def cell(text, after):       # type a cell, then Tab (next col) or Enter (next row)
    r4.type(text); time.sleep(0.35); r4.key(after); time.sleep(0.35)

try:
    snap("00_initial")
    # clear any leftovers (keyboard close; Alt+N dismisses a 'save?' prompt). Proven path.
    for _ in range(4):
        r4.combo("alt+space"); time.sleep(0.8)
        r4.key("c"); time.sleep(0.9)
        r4.combo("alt+n"); time.sleep(0.7)
    snap("01_cleaned")
    # launch Excel
    r4.combo("win+r"); time.sleep(1.3)
    r4.type("excel"); time.sleep(0.4)
    r4.key("enter"); time.sleep(9.0)            # Excel cold start is slow
    snap("02_launched")
    # start screen -> Blank workbook (visual grounding)
    xy = ground("click the 'Blank workbook' template on the Excel start screen")
    if xy:
        r4.move(*xy); r4.click(); time.sleep(3.5)
    snap("03_blank")
    # budget table (A1:B4), keyboard nav
    cell("Item", "tab");   cell("Cost", "enter")
    cell("Coffee", "tab"); cell("5", "enter")
    cell("Lunch", "tab");  cell("12", "enter")
    cell("Total", "tab");  cell("=SUM(B2:B3)", "enter")   # -> 17
    time.sleep(1.0)
    snap("04_entered")
    # grounding-click the total cell (dense-grid grounding test)
    xy = ground("click the cell showing the total number 17 at the bottom of the Cost column")
    if xy:
        r4.move(*xy); r4.click(); time.sleep(1.0)
    snap("05_total_selected")
    log("DONE")
except Exception as e:
    log(f"ERROR {e!r}")
finally:
    try:
        env.close(); log("rig closed")
    except Exception as ex:
        log(f"close err {ex!r}")
