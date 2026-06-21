"""Recover the practice sheet: UNDO my erroneous fills, then capture for precise column ID."""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")
from pico_env import PicoEnv
from cua_agent import make_agent
from executive import Executive, Verifier
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\recover_{TS}"; os.makedirs(OUT, exist_ok=True)
def log(m): print(m, flush=True); open(os.path.join(OUT,"log.txt"),"a").write(m+"\n")
def snap(env,n): open(os.path.join(OUT,n+".png"),"wb").write(env.observe()["screenshot"]); log("snap "+n)
env = PicoEnv(cam_index=0, screen_size=(1920,1080), show=False)
ag = make_agent("uitars", model="uitars-q4", history=1, temperature=0.0, screen_size=(1920,1080))
ex = Executive(env, ag, verifier=Verifier("qwen2.5vl:7b"))
r4 = env.r4
try:
    ex.dismiss_modal(1)
    foc = ex.confirm("Is a Microsoft Excel spreadsheet the active window?")
    log(f"excel focused: {foc}")
    if foc:
        for _ in range(7):
            r4.combo("ctrl+z"); time.sleep(0.6)     # undo my fills -> restore original
        r4.combo("ctrl+home"); time.sleep(0.7)
        snap(env, "00_after_undo")
    else:
        log("ABORT: not focused"); snap(env, "00b_nofocus")
    log("OUT "+OUT)
finally:
    env.close()
