"""one-shot S1 diagnostic: capture one frame, do ONE agent.predict, dump raw response + timing."""
import os, sys, time, cv2
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
sys.path.insert(0, REPO)
os.environ["OPENAI_BASE_URL"] = "http://192.168.0.155:11434/v1"
os.environ["OPENAI_API_KEY"] = "ollama"
from mm_agents.evocua.evocua_agent import EvoCUAAgent

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
frame = None
for _ in range(8):
    ok, f = cap.read()
    if ok: frame = f
    time.sleep(0.1)
ok, buf = cv2.imencode(".png", frame); png = buf.tobytes()
cap.release()
print("frame captured", frame.shape, "png bytes", len(png), flush=True)

agent = EvoCUAAgent(model="evocua-8b-q5-clean", prompt_style="S1", coordinate_type="qwen25",
                    resize_factor=28, max_history_turns=1, max_tokens=2048,
                    temperature=0.0, screen_size=(1920, 1080))
agent.reset()
t = time.time()
resp, codes = agent.predict("Using the open Calculator, compute 7 × 8 + 5",
                            {"screenshot": png, "instruction": None})
el = time.time() - t
with open("test_s1_out.txt", "w", encoding="utf-8") as fo:
    fo.write(f"elapsed={el:.1f}s\ncodes={codes}\nresp_len={len(resp) if resp else 0}\n---RESPONSE---\n{resp}\n")
print(f"DONE elapsed={el:.1f}s codes={codes}", flush=True)
