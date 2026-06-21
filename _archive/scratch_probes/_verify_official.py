import os,sys
REPO=os.path.join(os.getcwd(),"evocua");sys.path.insert(0,REPO)
os.environ["OPENAI_BASE_URL"]="http://192.168.0.155:11434/v1";os.environ["OPENAI_API_KEY"]="ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent     # PATCHED official agent, no subclass
from mm_agents.evocua.utils import process_image
CUR="runs/20260618_135046/frames/step_02.png";PREV="runs/20260618_135046/frames/step_01.png"
RESP_8='Action: Click the 8 key.\n<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [300, 627]}}</tool_call>'  # SAME-LINE
prev=process_image(open(PREV,"rb").read(),factor=32)[0]
ag=EvoCUAAgent(model="evocua-8b-q5-clean",max_tokens=512,temperature=0.01,top_p=0.9,prompt_style="S2",
   max_history_turns=1,screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
obs={"screenshot":open(CUR,"rb").read()};parsed=0
for i in range(10):
    ag.reset(); ag.actions=["Click 7","Click x","Click 8"]; ag.responses=[RESP_8]; ag.screenshots=[prev]
    _,codes=ag.predict("Using the open Calculator, compute 7 x 8 + 5",obs); parsed+=bool(codes)
    print(f"[{i}] {codes}",flush=True)
print(f"\nPATCHED official agent, same-line history: parsed={parsed}/10",flush=True)
