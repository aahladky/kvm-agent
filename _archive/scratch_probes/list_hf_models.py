import sys, os
sys.path.insert(0, r"C:\Dev\vllm")
from planner import HFPlanner
import openai
tok = HFPlanner._hf_token()
client = openai.OpenAI(base_url="https://router.huggingface.co/v1", api_key=tok)
ids = sorted(m.id for m in client.models.list().data)
print("total available:", len(ids))
def show(label, keys):
    hits = [i for i in ids if any(k in i.lower() for k in keys)]
    print(f"\n{label} ({len(hits)}):")
    for i in hits[:30]:
        print("  ", i)
show("VISION/VLM", ["vl", "vision", "llava", "pixtral", "gemma-3", "internvl", "smolvlm"])
show("strong TEXT (Qwen/Llama/Mistral/DeepSeek)", ["qwen2.5-72", "qwen2.5-32", "llama-3.3", "llama-3.1-70", "mistral-large", "deepseek"])
