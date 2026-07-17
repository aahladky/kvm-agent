"""
agent_server.py — entry point for the Open WebUI "computer-use-agent" server.

The FastAPI app now lives in kvm_agent/server/app.py (the single source of truth); this
file is just a thin launcher so `python agent_server.py [--mock]` keeps working. It used
to carry a full duplicate of the app and read its own env vars — consolidated 2026-06-21
so everything flows through kvm_agent/config.py (CFG).

Open WebUI (laptop) adds this as an OpenAI API connection:
    base_url = http://<DESKTOP_LAN_IP>:8088/v1     (this desktop = 192.168.0.184)
    api_key  = anything
Then pick the "computer-use-agent" model and type a GOAL.

Roles (configure via kvm_agent/config.py or the matching env vars):
    PLANNER   = AGENT_PLANNER (hf=Qwen3-VL-8B via HF | claude | rule) / AGENT_PLANNER_MODEL
    EXECUTOR  = UI-TARS on laptop Ollama (EXECUTOR_MODEL)
    VERIFIER  = qwen2.5vl on laptop Ollama (VERIFIER_MODEL)
    HID/CAPTURE = this desktop (holds the capture card + Pico)

Run:
    python agent_server.py --mock          # validate transport + Open WebUI wiring (no rig)
    python agent_server.py                 # real rig (boots camera+Pico on first task)
"""
import os, sys, argparse

# make the kvm_agent package importable when launched as a script from any cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kvm_agent.server.app import app, MODEL_ID, MOCK   # MOCK reads "--mock" from sys.argv at import

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    args, _ = ap.parse_known_args()
    import uvicorn
    print(f"[agent_server] mock={MOCK} host={args.host}:{args.port}  model={MODEL_ID}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
