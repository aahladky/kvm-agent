import os,sys,re
REPO=os.path.join(os.getcwd(),"evocua");sys.path.insert(0,REPO)
os.environ["OPENAI_BASE_URL"]="http://192.168.0.155:11434/v1";os.environ["OPENAI_API_KEY"]="ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent
from mm_agents.evocua.utils import process_image

class FixedAgent(EvoCUAAgent):
    def _parse_response_s2(self, response, *a, **k):
        if response:  # normalize tool_call delimiters onto their own lines
            response = re.sub(r"<tool_call>\s*", "<tool_call>\n", response)
            response = re.sub(r"\s*</tool_call>", "\n</tool_call>", response)
        return super()._parse_response_s2(response, *a, **k)

CUR="runs/20260618_135046/frames/step_02.png";PREV="runs/20260618_135046/frames/step_01.png"
# SAME-LINE history (the failing condition that dropped 12/12 unpatched)
RESP_8='Action: Click the 8 key.\n<tool_call>{"name": "computer_use", "arguments": {"action": "left_click", "coordinate": [300, 627]}}</tool_call>'
prev_b64=process_image(open(PREV,"rb").read(),factor=32)[0]
ag=FixedAgent(model="evocua-8b-q5-clean",max_tokens=512,temperature=0.01,top_p=0.9,prompt_style="S2",
   max_history_turns=1,screen_size=(1920,1080),coordinate_type="relative",resize_factor=32)
obs={"screenshot":open(CUR,"rb").read()}
parsed=0;coords=[]
for i in range(10):
    ag.reset(); ag.actions=["Click 7","Click x","Click 8"]; ag.responses=[RESP_8]; ag.screenshots=[prev_b64]
    _,codes=ag.predict("Using the open Calculator, compute 7 x 8 + 5",obs)
    parsed+=bool(codes); coords.append(str(codes)[:40])
    print(f"[{i}] {codes}",flush=True)
print(f"\nFIXED parser, SAME-LINE history: parsed={parsed}/10",flush=True)
