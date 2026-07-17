"""preflight.py — non-invasive rig readiness check before a live run_goal.

Checks the planner env, the laptop Ollama (executor + verifier), and whether the capture card is
already held by agent_server. Does NOT touch the Pico: a probe connection wedges its firmware
(CLAUDE.md) — the Executive's own R4() fast-fails (5s) if the Pico is down, so let it.
"""
import os, sys, json, socket, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kvm_agent.config import CFG

print("AGENT_PLANNER_MODEL env :", os.environ.get("AGENT_PLANNER_MODEL", "(unset -> CFG default)"))
print("CFG planner kind/model  :", CFG.planner_kind, "/", CFG.planner_model)
print("CFG planner max_tokens  :", CFG.planner_effective_max_tokens, "| thinking:", CFG.planner_thinking)

# Ollama (executor uitars + verifier qwen2.5vl) -----------------------------------------------
try:
    tags = json.load(urllib.request.urlopen(CFG.ollama_base + "/api/tags", timeout=5))
    names = sorted((m.get("name") or "") for m in tags.get("models", []))
    print("Ollama reachable        :", CFG.ollama_base)
    for n in (CFG.executor_model, CFG.verifier_model):
        hit = any(n.split(":")[0] in x for x in names)
        print(f"   need {n:16} present: {hit}")
except Exception as e:
    print("Ollama UNREACHABLE      :", CFG.ollama_base, "|", repr(e)[:120])

# capture card / agent_server: is something already holding port 8088? ------------------------
def port_open(host, port):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()

busy = port_open("127.0.0.1", 8088)
print("agent_server on :8088    :", busy, "(True => rig likely BUSY; stop it before running)")
print("Pico                    : NOT pre-checked by design (a probe wedges the firmware)")
print("READY" if not busy else "NOT READY (server holding the rig)")
