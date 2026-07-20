import os,sys,re
REPO=os.path.join(os.getcwd(),"evocua");sys.path.insert(0,REPO)
os.environ["OPENAI_BASE_URL"]="http://192.168.0.155:11434/v1";os.environ["OPENAI_API_KEY"]="ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent
from mm_agents.evocua.utils import process_image
CUR="runs/20260618_135046/frames/step_02.png";PREV="runs/20260618_135046/frames/step_01.png"
RESP_8='Action: Click the 8 key.\n<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [300, 627]}}</tool_call>'
MODEL=os.environ.get("PROBE_MODEL","evocua-8b-q5-clean");N=int(os.environ.get("PROBE_N","12"));T=float(os.environ.get("PROBE_T","0.01"))
prev_b64=process_image(open(PREV,"rb").read(),factor=32)[0]
ag=EvoCUAAgent(model=MODEL,max_tokens=512,temperature=T,top_p=0.9,prompt_style="S2",
   max_history_turns=1,screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
obs={"screenshot":open(CUR,"rb").read()}
same=new=parsed=drop=0
for i in range(N):
    ag.reset(); ag.actions=["Click 7","Click x","Click 8"]; ag.responses=[RESP_8]; ag.screenshots=[prev_b64]
    try:
        resp,codes=ag.predict("Using the open Calculator, compute 7 x 8 + 5",obs)
    except Exception as e:
        print(f"[{i}] EXC {e}",flush=True); continue
    m=re.search(r"<tool_call>([^\n]*)",resp or ""); after=(m.group(1).strip() if m else "")
    fmt="SAME" if after.startswith("{") else "NEW"
    same+=fmt=="SAME"; new+=fmt=="NEW"; parsed+=bool(codes); drop+= not bool(codes)
    print(f"[{i}] T={T} fmt={fmt:5s} codes={'Y' if codes else 'DROPPED'}  {str(codes)[:40]}",flush=True)
print(f"\n{MODEL} img-hist T={T}: same={same}/{N} new={new}/{N} | parsed={parsed} DROPPED={drop}",flush=True)
