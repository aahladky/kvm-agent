"""diag_router.py — what will the HF router actually serve THIS token, and the full pinned error.

Follow-up to diag_provider.py: (1) lists the router catalog for the token and greps for the model +
featherless, (2) re-runs the :featherless-ai pinned call and prints the FULL error body (the HTML the
500 returned — it usually names the real cause: provider not enabled / billing / gateway 502).
"""
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kvm_agent.config import CFG

BASE = (sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen3-VL-8B-Thinking").split(":")[0]
try:
    from huggingface_hub import get_token
    tok = get_token()
except Exception:
    tok = os.environ.get("HF_TOKEN")

# 1) router catalog for this token --------------------------------------------------------------
try:
    req = urllib.request.Request("https://router.huggingface.co/v1/models",
                                 headers={"Authorization": "Bearer " + tok} if tok else {})
    data = json.load(urllib.request.urlopen(req, timeout=30)).get("data", [])
    print("router catalog entries:", len(data))
    qwen_vl = [m.get("id") for m in data if "Qwen3-VL" in (m.get("id") or "")]
    print("Qwen3-VL ids served    :", qwen_vl or "(none)")
    feath = [m.get("id") for m in data if "featherless" in json.dumps(m).lower()]
    print("entries naming featherless:", len(feath), feath[:8])
    # provider list for the target model, as the router reports it
    for m in data:
        if m.get("id") == BASE:
            print("providers for", BASE, ":", json.dumps(m.get("providers", m), indent=2)[:600])
except Exception as e:
    print("router catalog error:", repr(e)[:200])

# 2) full pinned-call error body ----------------------------------------------------------------
try:
    import openai
    client = openai.OpenAI(base_url="https://router.huggingface.co/v1", api_key=tok or "none")
    try:
        r = client.chat.completions.create(model=BASE + ":featherless-ai", max_tokens=16,
                                            messages=[{"role": "user", "content": "say ok"}])
        print("pinned call OK:", r.choices[0].message.content)
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", None)
        print("pinned call ERROR type:", type(e).__name__)
        print("pinned call BODY:\n", (body or str(e))[:1500])
except Exception as e:
    print("openai error:", repr(e)[:200])
