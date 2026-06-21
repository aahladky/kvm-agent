import os,sys,re,json
REPO=os.path.join(os.getcwd(),"evocua");sys.path.insert(0,REPO)
os.environ["OPENAI_BASE_URL"]="http://192.168.0.155:11434/v1";os.environ["OPENAI_API_KEY"]="ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent
from mm_agents.evocua.utils import process_image
CUR="runs/20260618_135046/frames/step_02.png"
MODEL=os.environ.get("PROBE_MODEL","evocua-8b-q5-clean"); N=int(os.environ.get("PROBE_N","12"))
ag=EvoCUAAgent(model=MODEL,max_tokens=512,temperature=0.3,top_p=0.9,prompt_style="S2",
   max_history_turns=0,screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
obs={"screenshot":open(CUR,"rb").read()}
sameline=newline=parsed=dropped=0
for i in range(N):
    ag.reset(); ag.actions=["Click 7","Click x","Click 8"]
    resp,codes=ag.predict("Using the open Calculator, compute 7 x 8 + 5",obs)
    m=re.search(r"<tool_call>([^\n]*)",resp or "")
    after=(m.group(1).strip() if m else "")
    fmt="SAME" if after.startswith("{") else "NEWLINE"
    if fmt=="SAME": sameline+=1
    else: newline+=1
    if codes: parsed+=1
    else: dropped+=1
    print(f"[{i}] fmt={fmt:7s} parsed={'Y' if codes else 'N (DROPPED)'}",flush=True)
print(f"\n{MODEL}: same-line={sameline}/{N}  newline={newline}/{N}  | parsed={parsed}/{N}  DROPPED={dropped}/{N}",flush=True)
