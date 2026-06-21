"""Fix Profit% (col H): it's %-formatted, so store the RATIO (no *100) -> displays 15.73%."""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\fixH_{TS}"; os.makedirs(OUT, exist_ok=True)
def log(m): print(m, flush=True); open(os.path.join(OUT,"log.txt"),"a").write(m+"\n")
def snap(env,n): open(os.path.join(OUT,n+".png"),"wb").write(env.observe()["screenshot"]); log("snap "+n)
env = PicoEnv(cam_index=0, screen_size=(1920,1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920,1080))
ex = Executive(env, ag, verifier=Verifier("qwen2.5vl:7b"))
r4 = env.r4
try:
    ex.dismiss_modal(1)
    if not ex.confirm("Is a Microsoft Excel spreadsheet the active window?"):
        log("ABORT not focused"); snap(env,"nofocus")
    else:
        r4.combo("ctrl+home"); time.sleep(0.7)
        r4.combo("ctrl+g"); time.sleep(1.1); r4.type("H5:H300"); time.sleep(0.4); r4.key("enter"); time.sleep(0.8)
        r4.type('=IF(E5="","",(F5-E5)/E5)'); time.sleep(0.5); r4.combo("ctrl+enter"); time.sleep(0.9)
        log("refilled H5:H300 = =IF(E5=\"\",\"\",(F5-E5)/E5)")
        r4.combo("ctrl+home"); time.sleep(0.7); snap(env,"01_fixed")
        log("verify: " + str(ex.verifier._vision(env.observe()["screenshot"],
            "What value is in the Profit % column for the first data row (row 5)? Should be around 15.7%. Answer briefly.")))
    log("DONE")
finally:
    env.close()
