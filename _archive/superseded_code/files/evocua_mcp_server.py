#!/usr/bin/env python3
"""
evocua_mcp_server.py — expose the EvoCUA physical-rig rollout as MCP tools over
Streamable HTTP, so a user-facing model in Open WebUI / LibreChat (or any MCP host)
can DELEGATE computer-use tasks to EvoCUA-8B driving the KVM rig.

Why a job model and not one blocking tool
-----------------------------------------
EvoCUA is an AGENT, not a tool: one rollout runs many predict->act steps over
seconds-to-minutes, and one physical rig is strictly SERIAL and STATEFUL (real
desktop, no per-task VM reset). A single synchronous tool call would blow past host
timeouts (e.g. LibreChat's per-server `timeout`). So we expose a job model:

    start_computer_task(goal)            -> {job_id}        (returns immediately)
    get_task_status(job_id)              -> {status, ...}   (orchestrator polls)
    continue_task(job_id, reply)         -> {status}        (answer a mid-task question)
    cancel_task(job_id)                  -> {status}        (abort + release the Pico)
    get_task_screenshot(job_id)          -> <image>         (what the rig sees)

The bidirectional ("2-way street") channel
-------------------------------------------
The answer channel wired into the agent surfaces here: when EvoCUA emits a standalone
`answer` (the model asking a question mid-task), the rollout PAUSES at status
`awaiting_reply` and exposes the question. The orchestrator is "the user" from the
model's view -- it answers from the conversation it already has, or asks the human, then
calls continue_task(reply). A terminate-with-answer is returned as the task's result.

Honesty note: there is NO OCR ground-truth here (that gate is run_probe's calculator
microtask). `succeeded` means EvoCUA *terminated claiming success* -- it is self-reported
and unverified. If correctness matters, the orchestrator/human should confirm via
get_task_screenshot. The model's own claim is returned as `answer`.

Transport: Streamable HTTP -- native in Open WebUI v0.6.31+ and the recommended transport
for LibreChat. Host points at:  http://<this-host>:<port>/mcp

Run
---
    # MOCK: no hardware, no Ollama. Exercises the FULL job lifecycle end to end.
    # (This is the offline-probe layer from the FINDINGS methodology: validate the
    #  host<->server<->state-machine plumbing without tying up the capture card.)
    python evocua_mcp_server.py --mock

    # REAL: on the rig machine, with the EvoCUA Ollama endpoint reachable.
    python evocua_mcp_server.py --host 0.0.0.0 --port 8077 \
        --model evocua-8b-q5-clean --settle 1.0 --max-steps 25

Single-rig safety: this server is the SOLE owner of the capture card + Pico while it
runs. Don't run run_probe.py against the same rig at the same time. Only ONE task runs
at a time; a second start_computer_task is rejected while one is active.
"""
import os
import re
import sys
import json
import time
import base64
import argparse
import threading
from uuid import uuid4
from typing import Optional, Dict, Any, Annotated

from pydantic import Field
from mcp.server.fastmcp import FastMCP, Image

# ----------------------------------------------------------------------------- config
# The agent talks to EvoCUA via an OpenAI-compatible endpoint (your Ollama/vLLM server).
os.environ.setdefault("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
os.environ.setdefault("OPENAI_API_KEY", "ollama")

DEFAULTS = {
    "model": "evocua-8b-q5-clean",
    "prompt_style": "S2",
    "coordinate_type": "relative",
    "resize_factor": 32,
    "history": 4,
    "temperature": 0.01,
    "max_tokens": 2048,
    "settle": 1.0,
    "max_steps": 25,
}
MAX_EMPTY_STREAK = 3          # k consecutive zero-action steps -> abort (parse/format guard)
RECLICK_WINDOW = 6            # last N clicks collapsing to <=2 spots -> abort (stuck guard)
SCREEN_SIZE = (1920, 1080)

ACTIVE = ("running", "awaiting_reply")   # a job occupying the rig

# tiny embedded placeholder frame so MOCK mode needs ZERO image deps (no PIL/cv2)
_MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAHklEQVR4nO3BMQEAAADCoPVPbQ0P"
    "oAAAAAAAAAAAAAB4GhPAAAFkq0d8AAAAAElFTkSuQmCC"
)


def _extract_xy(cmd: str):
    m = re.search(r"pyautogui\.(?:click|moveTo|doubleClick|tripleClick|rightClick)\(\s*"
                  r"(?:x\s*=\s*)?(-?\d+)\s*,\s*(?:y\s*=\s*)?(-?\d+)", str(cmd))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _is_reclick_loop(recent):
    clusters = []
    for c in recent:
        if not any(abs(c[0] - k[0]) < 15 and abs(c[1] - k[1]) < 15 for k in clusters):
            clusters.append(c)
    return len(clusters) <= 2


# --------------------------------------------------------------------------- backends
# Both backends are "PicoEnv/EvoCUAAgent-shaped": the rollout below only touches
# agent.reset()/agent.predict()/agent.last_answer and env.observe()/env.step()/
# env.end_full_png()/env.close(). That keeps the real and mock paths identical to the
# rollout, the same way pico_env.py is "DesktopEnv-shaped".

class MockAgent:
    """Canned rollout that exercises every branch: a click, a mid-task QUESTION (to test
    awaiting_reply/continue), a type that echoes the reply, then terminate+answer."""
    def __init__(self):
        self.last_answer = None
        self._i = 0

    def reset(self, *a, **k):
        self.last_answer = None
        self._i = 0

    def predict(self, instruction, obs):
        self.last_answer = None
        i, self._i = self._i, self._i + 1
        reply_seen = "[user reply]" in instruction
        if i == 0:
            return ("Action: click the menu", ["pyautogui.click(120, 200)"])
        if i == 1 and not reply_seen:
            self.last_answer = "I see several files (a.txt, b.txt). Which should I open?"
            return ("Action: ask the user", ["ANSWER"])
        if reply_seen:
            tail = instruction.rsplit("[user reply]", 1)[-1].strip()
            self.last_answer = f"Opened per your reply: {tail!r}"
            return ("Action: done", ["DONE"])
        # fallback safety
        self.last_answer = "Completed."
        return ("Action: done", ["DONE"])


class MockEnv:
    def observe(self):
        return {"screenshot": _MOCK_PNG, "instruction": None}

    def step(self, action, pause=0.0):
        time.sleep(min(pause, 0.15))  # keep the mock lifecycle snappy
        return self.observe(), 0, action in ("DONE", "FAIL"), {}

    def end_full_png(self):
        return _MOCK_PNG

    def close(self):
        pass

    def safe_release(self):
        pass


def build_backend(mock: bool, cfg: dict):
    """Return (agent, env). Real deps (EvoCUAAgent/PicoEnv -> cv2/openai/r4_client) are
    imported LAZILY so mock mode runs on any machine, hardware or not."""
    if mock:
        return MockAgent(), MockEnv()
    # repo layout: patched agent at repo root, evocua/ pristine on sys.path (matches operate.py)
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evocua")
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from evocua_agent import EvoCUAAgent          # patched copy at repo root
    from pico_env import PicoEnv
    agent = EvoCUAAgent(
        model=cfg["model"], max_tokens=cfg["max_tokens"], top_p=0.9,
        temperature=cfg["temperature"], action_space="pyautogui",
        observation_type="screenshot", max_steps=cfg["max_steps"],
        prompt_style=cfg["prompt_style"], max_history_turns=cfg["history"],
        screen_size=SCREEN_SIZE, coordinate_type=cfg["coordinate_type"],
        resize_factor=cfg["resize_factor"],
        # answer_in_schema stays False (frozen contract); capture is always-on. Flip to
        # True here only as a deliberate, measured second intervention.
    )
    env = PicoEnv(cam_index=0, screen_size=SCREEN_SIZE, show=False)
    # PicoEnv has no safe_release(); add the same "release any held button" guard close() uses.
    if not hasattr(env, "safe_release"):
        env.safe_release = lambda: env.r4.up()
    return agent, env


# ------------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, goal: str, max_steps: int):
        self.id = "job_" + uuid4().hex[:12]
        self.goal = goal
        self.max_steps = max_steps
        self.created_at = time.time()
        # progress
        self.status = "running"        # running | awaiting_reply | succeeded | failed | aborted | cancelled
        self.step = 0
        self.last_action: Optional[str] = None
        self.question: Optional[str] = None     # set while awaiting_reply
        self.answer: Optional[str] = None       # model's reported result at terminate
        self.detail: Optional[str] = None       # abort reason / error string
        self.screenshot: Optional[bytes] = None # latest PNG bytes
        # coordination
        self.cancel = threading.Event()
        self._reply_event = threading.Event()
        self._pending_reply: Optional[str] = None
        self._lock = threading.Lock()

    # -- worker side --
    def await_reply(self, question: str) -> Optional[str]:
        """Block the rollout until continue_task supplies a reply. Returns the reply
        (possibly ""), or None if the job was cancelled while waiting."""
        with self._lock:
            self.question = question
            self.status = "awaiting_reply"
            self._reply_event.clear()
        while not self._reply_event.wait(timeout=0.5):
            if self.cancel.is_set():
                return None
        with self._lock:
            self.status = "running"
            self.question = None
            r, self._pending_reply = self._pending_reply, None
        return r

    def set_terminal(self, status: str, *, answer=None, detail=None):
        with self._lock:
            self.status = status
            if answer is not None:
                self.answer = answer
            if detail is not None:
                self.detail = detail

    # -- tool side --
    def provide_reply(self, reply: str) -> bool:
        with self._lock:
            if self.status != "awaiting_reply":
                return False
            self._pending_reply = reply
            self._reply_event.set()
            return True

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.id, "status": self.status, "step": self.step,
                "last_action": self.last_action, "question": self.question,
                "answer": self.answer, "detail": self.detail,
                "goal": self.goal, "max_steps": self.max_steps,
                "age_seconds": round(time.time() - self.created_at, 1),
                "is_terminal": self.status in ("succeeded", "failed", "aborted", "cancelled"),
            }


class JobManager:
    """Single-rig, single-slot scheduler. Owns the shared agent+env; runs one rollout at
    a time in a daemon thread."""
    def __init__(self, agent, env, settle: float, default_max_steps: int):
        self.agent = agent
        self.env = env
        self.settle = settle
        self.default_max_steps = default_max_steps
        self.current: Optional[Job] = None
        self.jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, goal: str, max_steps: Optional[int]) -> Job:
        with self._lock:
            if self.current and self.current.status in ACTIVE:
                raise RuntimeError(self.current.id)   # rig busy; carries the active job id
            job = Job(goal, max_steps or self.default_max_steps)
            # bound memory: drop the previous job's frame bytes once a new task starts
            if self.current is not None:
                self.current.screenshot = None
            self.current = job
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self.jobs.get(job_id)

    def _run(self, job: Job):
        try:
            self._rollout(job)
        except Exception as e:
            job.set_terminal("failed", detail=f"{type(e).__name__}: {e}")
        finally:
            try:
                self.env.safe_release()        # release any held button between tasks
            except Exception:
                pass

    def _rollout(self, job: Job):
        """Mirror of operate.run_goal: same sentinels, same answer-aware empty guard and
        re-click guard -- but pauses on ANSWER for continue_task instead of input()."""
        agent, env = self.agent, self.env
        agent.reset()
        obs = env.observe()                    # current screen; NO benchmark reset-click
        goal = job.goal
        recent = []
        empty_streak = 0

        for it in range(job.max_steps):
            if job.cancel.is_set():
                return job.set_terminal("cancelled", detail="cancelled by user")
            try:
                response, actions = agent.predict(goal, obs)
            except Exception as e:
                return job.set_terminal("failed", detail=f"predict error: {e}")
            job.step = it

            # answer-aware empty-action guard (a communicative step is never "empty")
            if not actions and not getattr(agent, "last_answer", None):
                empty_streak += 1
                if empty_streak >= MAX_EMPTY_STREAK:
                    return job.set_terminal("aborted", detail="empty_action_streak")
                continue
            empty_streak = 0

            for action in actions:
                if job.cancel.is_set():
                    return job.set_terminal("cancelled", detail="cancelled by user")

                if action in ("DONE", "FAIL"):
                    claim = getattr(agent, "last_answer", None)
                    job.screenshot = env.end_full_png()
                    if action == "DONE":
                        return job.set_terminal("succeeded", answer=claim,
                                                detail="model terminated: success (self-reported, unverified)")
                    return job.set_terminal("failed", answer=claim,
                                            detail="model terminated: failure")

                if action == "ANSWER":                 # the 2-way street
                    q = getattr(agent, "last_answer", None) or ""
                    reply = job.await_reply(q)
                    if reply is None:                  # cancelled while waiting
                        return job.set_terminal("cancelled", detail="cancelled while awaiting reply")
                    if reply:
                        goal = f"{goal}\n\n[user reply] {reply}"   # folded into next predict
                    continue

                if action == "WAIT":
                    obs, _, _, _ = env.step(action, self.settle)
                    continue

                # normal GUI action -> drive the rig
                obs, _, _, _ = env.step(action, self.settle)
                job.last_action = action
                job.screenshot = env.end_full_png()
                xy = _extract_xy(action)
                if xy:
                    recent = (recent + [xy])[-RECLICK_WINDOW:]
                    if len(recent) >= RECLICK_WINDOW and _is_reclick_loop(recent):
                        return job.set_terminal("aborted", detail="reclick_loop")

        job.set_terminal("aborted", detail=f"max_steps ({job.max_steps}) reached without terminate")


# --------------------------------------------------------------------------- MCP server
mcp = FastMCP("evocua_mcp")
MANAGER: Optional[JobManager] = None      # populated in main() after the backend is built


def _need_manager() -> JobManager:
    if MANAGER is None:                   # only possible if tools are called before run()
        raise RuntimeError("server not initialized")
    return MANAGER


GoalArg = Annotated[str, Field(
    min_length=1, max_length=2000,
    description="Natural-language computer-use task for EvoCUA to perform on the rig, e.g. "
                "'open Calculator and compute 7 x 8 + 5'. The rig keeps its desktop state between "
                "tasks; the agent starts from the current screen (no reset).")]
MaxStepsArg = Annotated[Optional[int], Field(
    ge=1, le=200, description="Optional cap on agent steps for this task (default from server config).")]
JobIdArg = Annotated[str, Field(
    min_length=1, description="The job_id returned by start_computer_task.")]
ReplyArg = Annotated[str, Field(
    max_length=2000,
    description="Your answer to the agent's question, folded into its next step as guidance. "
                "May be empty to let it proceed on its own.")]


@mcp.tool(
    name="start_computer_task",
    annotations={"title": "Start an EvoCUA computer-use task",
                 "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
def start_computer_task(goal: GoalArg, max_steps: MaxStepsArg = None) -> str:
    """Delegate one computer-use task to EvoCUA on the physical rig. Returns IMMEDIATELY
    with a job_id; the rollout runs in the background (seconds to minutes). Poll progress
    with get_task_status. DRIVES REAL MOUSE/KEYBOARD with no undo, and the rig is shared
    and serial: if a task is already active this is rejected -- poll or cancel it first.

    Returns JSON: {"job_id": str, "status": "running"} on success,
    or {"error": str, "active_job_id": str, "active_status": str} if the rig is busy.
    """
    mgr = _need_manager()
    try:
        job = mgr.start(goal, max_steps)
    except RuntimeError as busy:
        active = mgr.get(str(busy))
        return json.dumps({
            "error": "Rig busy: another task is active. Poll it with get_task_status or stop it "
                     "with cancel_task, then retry.",
            "active_job_id": str(busy),
            "active_status": active.snapshot()["status"] if active else "unknown",
        })
    return json.dumps({"job_id": job.id, "status": "running",
                       "note": "Poll get_task_status. If status becomes 'awaiting_reply', the agent "
                               "is asking a question -- answer it with continue_task."})


@mcp.tool(
    name="get_task_status",
    annotations={"title": "Get EvoCUA task status",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
def get_task_status(job_id: JobIdArg) -> str:
    """Poll a task. Call repeatedly until "is_terminal" is true.

    Returns JSON with:
      status     : running | awaiting_reply | succeeded | failed | aborted | cancelled
      step       : current agent step index
      last_action: last GUI action executed (pyautogui string) or null
      question   : if status=awaiting_reply, the agent's question -> answer via continue_task
      answer     : model's self-reported result at terminate (UNVERIFIED -- confirm with
                   get_task_screenshot if correctness matters) or null
      detail     : abort reason / error / termination note
      is_terminal: whether the task has finished
    """
    job = _need_manager().get(job_id)
    if job is None:
        return json.dumps({"error": f"Unknown job_id {job_id!r}."})
    return json.dumps(job.snapshot())


@mcp.tool(
    name="continue_task",
    annotations={"title": "Answer an EvoCUA mid-task question and resume",
                 "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
def continue_task(job_id: JobIdArg, reply: ReplyArg) -> str:
    """Resume a task that is paused at status 'awaiting_reply' by supplying the agent's
    requested answer. Resuming continues real GUI actions. If the task isn't awaiting a
    reply, this is a no-op error.

    Returns JSON: {"job_id": str, "status": "running", "accepted_reply": str}
    or {"error": str, "status": <current>} if the task isn't awaiting a reply.
    """
    job = _need_manager().get(job_id)
    if job is None:
        return json.dumps({"error": f"Unknown job_id {job_id!r}."})
    if not job.provide_reply(reply):
        return json.dumps({"error": "Task is not awaiting a reply; nothing to continue.",
                           "status": job.snapshot()["status"]})
    return json.dumps({"job_id": job.id, "status": "running", "accepted_reply": reply})


@mcp.tool(
    name="cancel_task",
    annotations={"title": "Cancel an EvoCUA task",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
def cancel_task(job_id: JobIdArg) -> str:
    """Request cancellation of a task. The rollout stops at the next step boundary (or
    immediately if it is awaiting a reply) and the rig's mouse button is released.

    Returns JSON: {"job_id": str, "status": <current>, "cancelling": bool}
    """
    job = _need_manager().get(job_id)
    if job is None:
        return json.dumps({"error": f"Unknown job_id {job_id!r}."})
    snap = job.snapshot()
    if snap["is_terminal"]:
        return json.dumps({"job_id": job.id, "status": snap["status"], "cancelling": False,
                           "note": "Task already finished."})
    job.cancel.set()
    return json.dumps({"job_id": job.id, "status": "cancelling", "cancelling": True})


@mcp.tool(
    name="get_task_screenshot",
    annotations={"title": "Get the rig's current screen for a task",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": False},
)
def get_task_screenshot(job_id: JobIdArg) -> Image:
    """Return the latest captured frame for a task as a PNG image -- use this to VERIFY a
    self-reported 'succeeded', or to see what the agent is looking at. Falls back to a
    live capture if no per-step frame has been stored yet."""
    mgr = _need_manager()
    job = mgr.get(job_id)
    png = None
    if job is not None:
        png = job.screenshot
    if png is None:
        try:
            png = mgr.env.end_full_png()
        except Exception:
            png = _MOCK_PNG
    return Image(data=png, format="png")


# ------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="EvoCUA rig as an MCP server (Streamable HTTP).")
    ap.add_argument("--mock", action="store_true",
                    help="run with a canned backend (no hardware/Ollama) to test the lifecycle")
    ap.add_argument("--host", default=os.environ.get("EVOCUA_MCP_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVOCUA_MCP_PORT", "8077")))
    ap.add_argument("--path", default=os.environ.get("EVOCUA_MCP_PATH", "/mcp"),
                    help="Streamable-HTTP mount path (host connects to http://HOST:PORT<path>)")
    ap.add_argument("--model", default=DEFAULTS["model"])
    ap.add_argument("--settle", type=float, default=DEFAULTS["settle"])
    ap.add_argument("--max-steps", type=int, default=DEFAULTS["max_steps"])
    ap.add_argument("--history", type=int, default=DEFAULTS["history"])
    args = ap.parse_args()

    cfg = dict(DEFAULTS, model=args.model, settle=args.settle,
               max_steps=args.max_steps, history=args.history)

    global MANAGER
    agent, env = build_backend(args.mock, cfg)
    MANAGER = JobManager(agent, env, settle=args.settle, default_max_steps=args.max_steps)

    # Stateless JSON Streamable HTTP: simplest to scale / friendliest across hosts. Our own
    # state lives in the JobManager, keyed by job_id, so the transport needs no session.
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = args.path
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True

    mode = "MOCK (no hardware)" if args.mock else f"REAL rig, model={args.model}"
    print(f"[evocua_mcp] {mode}")
    print(f"[evocua_mcp] Streamable HTTP at http://{args.host}:{args.port}{args.path}")
    print(f"[evocua_mcp] point Open WebUI (Admin > External Tools > MCP Streamable HTTP) or")
    print(f"[evocua_mcp] LibreChat (librechat.yaml mcpServers: type: streamable-http) at that URL.")
    try:
        mcp.run(transport="streamable-http")
    finally:
        try:
            env.close()
        except Exception:
            pass
        print("[evocua_mcp] hardware released. bye.")


if __name__ == "__main__":
    main()
