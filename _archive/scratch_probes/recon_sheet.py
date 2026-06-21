"""Recon the practice sheet: confirm focus, find data extent (Ctrl+End), read headers. No edits."""
import sys, os, time
sys.path.insert(0, r"C:\Dev\vllm")
from pico_env import PicoEnv
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\recon_{TS}"; os.makedirs(OUT, exist_ok=True)
def snap(env, n): open(os.path.join(OUT, n + ".png"), "wb").write(env.observe()["screenshot"]); print("snap", n, flush=True)
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
r4 = env.r4
try:
    snap(env, "00_current")
    r4.combo("ctrl+home"); time.sleep(1.0); snap(env, "01_home")   # if focused -> A1
    r4.combo("ctrl+end");  time.sleep(1.0); snap(env, "02_end")    # last used cell (Name box shows it)
    r4.combo("ctrl+home"); time.sleep(0.6)                         # back to A1, leave tidy
    print("OUT", OUT, flush=True)
finally:
    env.close()
