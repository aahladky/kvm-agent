"""Probe whether Excel is installed: open Start, type 'excel', snap (NO Enter), Esc. Safe."""
import sys, os, time, json
sys.path.insert(0, r"C:\Dev\vllm")
from pico_env import PicoEnv
TS = time.strftime("%Y%m%d_%H%M%S")
OUT = rf"C:\Dev\vllm\runs\probe_excel_{TS}"; os.makedirs(OUT, exist_ok=True)
def snap(env, name):
    open(os.path.join(OUT, name + ".png"), "wb").write(env.observe()["screenshot"]); print("snap", name, flush=True)
env = PicoEnv(cam_index=0, screen_size=(1920, 1080), show=False)
r4 = env.r4
try:
    r4.key("esc"); time.sleep(0.6)          # dismiss the leftover 'cannot find excel' dialog
    r4.key("esc"); time.sleep(0.6)
    snap(env, "00_desktop")
    r4.key("win"); time.sleep(1.5)          # open Start
    r4.type("excel"); time.sleep(2.0)       # Start search (finds installed apps)
    snap(env, "01_start_excel")
    r4.key("esc"); time.sleep(0.6)          # close Start (NO Enter)
    print("OUT", OUT, flush=True)
finally:
    env.close()
