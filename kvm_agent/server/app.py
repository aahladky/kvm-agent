"""
agent_server.py — the computer-use agent as an OpenAI-compatible model, for Open WebUI.

Open WebUI (on the laptop) adds this as an "OpenAI API connection":
    base_url = http://<DESKTOP_LAN_IP>:8088/v1     (this desktop = 192.168.0.184)
    api_key  = anything
Then pick the "computer-use-agent" model and type a GOAL. The server runs
planner.run_goal(executive) on the physical rig and STREAMS live progress back into chat.

Roles (all behind one chat box):
    PLANNER   = HFPlanner (default Qwen3-VL-8B via HF; env AGENT_PLANNER / AGENT_PLANNER_MODEL)
    EXECUTOR  = UI-TARS on laptop Ollama (env EXECUTOR_MODEL)
    VERIFIER  = qwen2.5vl on laptop Ollama (env VERIFIER_MODEL)
    HID/CAPTURE = this desktop (holds the capture card + Pico)

One task at a time (the rig is a single shared resource). `--mock` skips the rig entirely
(fake progress) so the Open WebUI wiring can be validated first.

Run:
    python agent_server.py --mock          # validate transport + Open WebUI wiring
    python agent_server.py                 # real rig (boots camera+Pico on first task)
"""
import os, sys, json, time, uuid, queue, threading, argparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

MODEL_ID = "computer-use-agent"
MOCK = "--mock" in sys.argv

app = FastAPI(title="Computer-Use Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_RIG_LOCK = threading.Lock()      # one task at a time (single rig)
_EXEC = None                       # lazily-opened Executive (holds camera+Pico)
_PLANNER = None


def build_planner():
    kind = os.environ.get("AGENT_PLANNER", "hf").lower()
    model = os.environ.get("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    send_image = os.environ.get("AGENT_SEND_IMAGE", "1") != "0"
    if kind == "hf":
        from kvm_agent.orchestration.planner import HFPlanner
        return HFPlanner(model=model, send_image=send_image)
    if kind == "claude":
        from kvm_agent.orchestration.planner import ClaudePlanner
        return ClaudePlanner()
    from kvm_agent.orchestration.planner import RulePlanner
    return RulePlanner()


def get_executive():
    global _EXEC, _PLANNER
    if _PLANNER is None:
        _PLANNER = build_planner()
    if _EXEC is None:
        from kvm_agent.orchestration.executive import Executive, Verifier
        _EXEC = Executive.open(
            executor_model=os.environ.get("EXECUTOR_MODEL", "uitars-q4"),
            verifier=Verifier(os.environ.get("VERIFIER_MODEL", "qwen2.5vl:7b")),
            log_dir=r"C:\Dev\vllm\runs")
    return _EXEC, _PLANNER


def extract_goal(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c
                                if isinstance(p, dict) and p.get("type") == "text").strip()
    return ""


def _chunk(content=None, role=None, finish=None):
    delta = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    payload = {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion.chunk",
               "created": int(time.time()), "model": MODEL_ID,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return "data: " + json.dumps(payload) + "\n\n"


def _run_worker(goal, q, out):
    """Run the task; push short progress strings into q; sentinel None at the end."""
    try:
        if MOCK:
            for m in ["booting rig (mock)…", "resetting desktop…", "planning…",
                      "launch notepad  ok", 'type "hello world"  ok', "launch calc  ok",
                      "type 6*7  ok", "verify 42  ok (read 42)", "done", "finished: done"]:
                q.put(m); time.sleep(0.4)
            out["summary"] = "(mock) task complete — wiring works."
            return
        if not _RIG_LOCK.acquire(blocking=False):
            q.put("the rig is busy with another task — try again shortly."); return
        try:
            # The old "check Pico injector" pre-connect (create_connection+close) WEDGED
            # the firmware: the Pico serve loop blocks on recv_into with settimeout(None)
            # and CircuitPython doesn't wake promptly on the peer FIN, so the closed check
            # connection left the loop stuck and the Executive's real connection was never
            # accept()ed -> first command no-op'd, then WinError 10054. Use ONE connection:
            # the Executive's own R4() already fast-fails (5s) if the Pico is unreachable.
            q.put("booting rig (camera + Pico) if needed…")
            from kvm_agent.orchestration.planner import run_goal
            try:
                ex, planner = get_executive()
            except (ConnectionError, OSError):
                q.put("Pico injector OFFLINE (192.168.0.183:8000 unreachable). "
                      "Power-cycle the Pico / wake the target machine, then retry.")
                return
            r = run_goal(goal, planner, ex, on_event=lambda m: q.put(m))
            out["result"] = r
            out["summary"] = (f"Task **{r.get('status')}** in {r.get('elapsed','?')}s "
                              f"({r.get('replans', 0)} re-plan(s)).")
        finally:
            _RIG_LOCK.release()
    except Exception as e:
        q.put(f"ERROR: {e!r}"); out["summary"] = f"error: {e!r}"
    finally:
        q.put(None)


def sse_stream(goal):
    q, out = queue.Queue(), {}
    threading.Thread(target=_run_worker, args=(goal, q, out), daemon=True).start()
    yield _chunk(role="assistant")
    yield _chunk(content=f"**Goal:** {goal}\n\n")
    while True:
        m = q.get()
        if m is None:
            break
        yield _chunk(content=f"- {m}\n")
    yield _chunk(content="\n" + out.get("summary", "done"))
    yield _chunk(finish="stop")
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
@app.get("/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model",
            "created": int(time.time()), "owned_by": "rig"}]}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    goal = extract_goal(body)
    if body.get("stream"):
        return StreamingResponse(sse_stream(goal), media_type="text/event-stream")
    # non-streaming: run to completion, return one message
    q, out = queue.Queue(), {}
    t = threading.Thread(target=_run_worker, args=(goal, q, out), daemon=True)
    t.start()
    lines = []
    while True:
        m = q.get()
        if m is None:
            break
        lines.append(m)
    content = f"**Goal:** {goal}\n\n" + "\n".join(f"- {x}" for x in lines) + \
              "\n\n" + out.get("summary", "done")
    return JSONResponse({"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion",
            "created": int(time.time()), "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})


@app.get("/")
@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_ID, "mock": MOCK,
            "planner": os.environ.get("AGENT_PLANNER", "hf"),
            "planner_model": os.environ.get("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-8B-Instruct")}


@app.on_event("shutdown")
def _shutdown():
    global _EXEC
    if _EXEC is not None:
        try:
            _EXEC.close()
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    args, _ = ap.parse_known_args()
    import uvicorn
    print(f"[agent_server] mock={MOCK} host={args.host}:{args.port}  model={MODEL_ID}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
