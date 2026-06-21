"""Probe Excel's launch state after install (no typing). Boot -> clean -> launch -> describe."""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier

TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\probe_excel2_{TS}"; os.makedirs(OUT, exist_ok=True)
def log(m): print(m, flush=True); open(os.path.join(OUT,"log.txt"),"a").write(m+"\n")
def snap(env,n): open(os.path.join(OUT,n+".png"),"wb").write(env.observe()["screenshot"]); log("snap "+n)

env = PicoEnv(cam_index=0, screen_size=(1920,1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920,1080))
ex = Executive(env, ag, verifier=Verifier("qwen2.5vl:7b"))
r4 = env.r4
try:
    ex.dismiss_modal(2)
    log("reset: " + str(ex.reset_clean(max_close=6)))
    snap(env, "00_clean")
    r4.combo("win+r"); time.sleep(1.3); r4.type("excel"); time.sleep(0.4)
    r4.key("enter"); time.sleep(15.0)             # first launch post-install is slow
    snap(env, "01_excel")
    desc = ex.verifier._vision(env.observe()["screenshot"],
        "Describe this screen in one sentence. Is it the Excel start screen (templates), "
        "an open blank spreadsheet, a sign-in or license-agreement dialog, or an error?")
    log("STATE: " + str(desc))
    time.sleep(3); snap(env, "02_excel")
    log("OUT " + OUT)
finally:
    env.close(); log("closed")
