import os, sys, json, re
import numpy as np
from PIL import Image
from io import BytesIO
REPO=os.path.join(os.path.dirname(os.path.abspath(__file__)),"evocua"); sys.path.insert(0,REPO)
os.environ["OPENAI_BASE_URL"]="http://192.168.0.155:11434/v1"; os.environ["OPENAI_API_KEY"]="ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent
from mm_agents.evocua.utils import process_image

INSTR="Using the open Calculator, compute 7 x 8 + 5"
N=int(os.environ.get("PROBE_N","8")); MODEL=os.environ.get("PROBE_MODEL","evocua-8b-q5-clean")
OP_X=677; DG_X=578  # real button columns (1920px)

def b64(p): return process_image(open(p,"rb").read(),factor=32)[0]
def png(p): return open(p,"rb").read()
def xy_of(codes):
    for c in codes:
        m=re.search(r"\((\d+),\s*(\d+)",str(c))
        if m: return int(m.group(1)),int(m.group(2))
    return None

CUR="runs/20260618_135046/frames/step_02.png"     # display 7x8 (decide +)
PREV="runs/20260618_135046/frames/step_01.png"    # display 7x  (when it clicked 8)

# realistic prior assistant responses with REAL grid coords
RESP_8_DIGIT='Action: Click the 8 key.\n<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[300,627]}}</tool_call>'
RESP_MUL_OP ='Action: Click the multiplication button.\n<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[350,627]}}</tool_call>'

def trial(cond):
    ag=EvoCUAAgent(model=MODEL,max_tokens=512,temperature=0.01,top_p=0.9,prompt_style="S2",
                   max_history_turns=cond["h"],screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
    obs={"screenshot":png(CUR)}; rows=[]
    for i in range(N):
        ag.reset()
        ag.actions=cond["actions"]; ag.responses=cond["responses"]; ag.screenshots=cond["screens"]
        _,codes=ag.predict(INSTR,obs); xy=xy_of(codes)
        col="OP" if (xy and abs(xy[0]-OP_X)<abs(xy[0]-DG_X)) else ("DIGIT" if xy else "?")
        rows.append({"xy":xy,"col":col}); print(f"  {cond['name']:22s}[{i}] {xy} {col}",flush=True)
    n=sum(r["col"]=="OP" for r in rows); print(f"  >>> {cond['name']}: {n}/{N} OP\n",flush=True)
    return {"cond":cond["name"],"n_op":n,"N":N,"rows":rows}

prev_b64=b64(PREV)
CONDS=[
 {"name":"h0_text_only","h":0,"actions":["Click 7","Click x","Click 8"],"responses":[],"screens":[]},
 {"name":"h1_prevDIGIT(8)","h":1,"actions":["Click 7","Click x","Click 8"],
  "responses":[RESP_8_DIGIT],"screens":[prev_b64]},
 {"name":"h1_prevOP(x)","h":1,"actions":["Click 7","Click x","Click 8"],
  "responses":[RESP_MUL_OP],"screens":[prev_b64]},
]
out=[trial(c) for c in CONDS]
json.dump(out,open("runs/_hist_probe_result.json","w"),indent=1)
print("=== SUMMARY ("+MODEL+") ===",flush=True)
for o in out: print(f"  {o['cond']:22s} {o['n_op']}/{o['N']} operator-col",flush=True)
