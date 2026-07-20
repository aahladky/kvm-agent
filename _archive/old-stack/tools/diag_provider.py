"""diag_provider.py — isolate WHY the HF router rejected a model (provider/token mismatch).

No rig, no HID. Prints: which token the planner actually resolves + who it authenticates as, the
router's provider mapping for the model, and the result of a minimal chat call both BARE and pinned
to :featherless-ai. That separates "wrong/old token (not the account with featherless)" from
"routing needs an explicit provider" from "provider enabled but model genuinely unavailable".

    python tools\diag_provider.py
    python tools\diag_provider.py Qwen/Qwen3-VL-8B-Thinking
"""
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kvm_agent.config import CFG

MODEL = (sys.argv[1] if len(sys.argv) > 1
         else os.environ.get("AGENT_PLANNER_MODEL", CFG.planner_model))
BASE = MODEL.split(":")[0]

# 1) which token does the planner resolve, and who is it? -------------------------------------
tok = None
try:
    from huggingface_hub import get_token
    tok = get_token()
except Exception as e:
    print("get_token error:", repr(e))
if not tok:
    for v in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
        if os.environ.get(v):
            tok = os.environ[v]
            break
env_set = [v for v in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGINGFACEHUB_API_TOKEN")
           if os.environ.get(v)]
print("MODEL          :", MODEL)
print("token present  :", bool(tok), "| prefix:", (tok[:7] + "…" if tok else None))
print("env token vars :", env_set or "(none — using cached huggingface-cli login)")
if tok:
    try:
        from huggingface_hub import whoami
        w = whoami(token=tok)
        print("authenticates  :", w.get("name"), "| type:", w.get("type"),
              "| email:", w.get("email"))
    except Exception as e:
        print("whoami error   :", repr(e)[:200])

# 2) router's provider mapping for this model (auth'd) ----------------------------------------
try:
    req = urllib.request.Request(
        "https://huggingface.co/api/models/" + BASE + "?expand=inferenceProviderMapping",
        headers={"Authorization": "Bearer " + tok} if tok else {})
    pm = json.load(urllib.request.urlopen(req, timeout=30)).get("inferenceProviderMapping", {})
    print("provider map   :", json.dumps(pm))
except Exception as e:
    print("provider map error:", repr(e)[:200])

# 3) minimal chat call: bare model, then pinned to featherless-ai -----------------------------
try:
    import openai
    client = openai.OpenAI(base_url="https://router.huggingface.co/v1", api_key=tok or "none")
    for m in (MODEL, BASE + ":featherless-ai"):
        try:
            r = client.chat.completions.create(
                model=m, max_tokens=16, messages=[{"role": "user", "content": "say ok"}])
            print(f"call {m:48} -> OK : {(r.choices[0].message.content or '')[:40]!r}")
        except Exception as e:
            print(f"call {m:48} -> ERR: {repr(e)[:240]}")
except Exception as e:
    print("openai client error:", repr(e)[:200])
