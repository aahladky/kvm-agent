# EvoCUA MCP server

Exposes the EvoCUA physical-rig rollout as **MCP tools over Streamable HTTP**, so a
user-facing model in **Open WebUI** or **LibreChat** (or any MCP host) can delegate
computer-use tasks to EvoCUA-8B driving the KVM rig.

Two models, one loop: the host's conversational model plans and talks to you; EvoCUA-8B
is the hands. EvoCUA is an *agent*, not a tool — so the server wraps the whole rollout
behind a **job model** rather than a single blocking call.

```
You ─▶ Open WebUI / LibreChat (orchestrator LLM)
            │  MCP (Streamable HTTP)
            ▼
     evocua_mcp_server.py ──▶ EvoCUAAgent ──▶ EvoCUA-8B (Ollama/vLLM)
            │
            └────────────────▶ PicoEnv ──▶ HDMI capture + Pico HID (the rig)
```

## Tools

| tool | what it does |
|---|---|
| `start_computer_task(goal, max_steps?)` | starts a rollout, returns a `job_id` immediately |
| `get_task_status(job_id)` | poll: `running / awaiting_reply / succeeded / failed / aborted / cancelled` |
| `continue_task(job_id, reply)` | answer a mid-task question (status `awaiting_reply`) and resume |
| `cancel_task(job_id)` | stop a task and release the Pico |
| `get_task_screenshot(job_id)` | latest rig frame as a PNG (use to verify a result) |

**The job model is what makes timeouts a non-issue.** A rollout can run for minutes, but
every tool call returns in well under a second (`start` hands back a `job_id`; `poll`
reads in-memory state). So you do **not** need to raise the host's per-tool timeout — the
orchestrator just polls.

**The 2-way street.** When EvoCUA emits a standalone `answer` (a question mid-task), the
job pauses at `awaiting_reply` and exposes `question`. The orchestrator is "the user": it
answers from the conversation it already has, or asks you, then calls `continue_task`.

## Run

```bash
# MOCK — no hardware, no Ollama. Exercises the FULL lifecycle (start → awaiting_reply →
# continue → succeeded) so you can validate the host↔server wiring before touching the rig.
python evocua_mcp_server.py --mock

# REAL — on the rig machine, EvoCUA Ollama endpoint reachable (defaults to your
# 192.168.0.155:11434; override with OPENAI_BASE_URL).
python evocua_mcp_server.py --host 0.0.0.0 --port 8077 \
    --model evocua-8b-q5-clean --settle 1.0 --max-steps 25
```

Deps: `pip install mcp` (plus your existing rig deps for real mode: the agent's
`openai`/`backoff`/`pillow`, `opencv-python`, and `r4_client`). Mock mode needs only `mcp`.

Layout assumption (same as `operate.py`): the patched `evocua_agent.py` + `pico_env.py`
sit next to this file at the repo root, with the pristine `evocua/` package on the path.

## Connect Open WebUI

Open WebUI has native MCP from **v0.6.31+**, Streamable-HTTP transport only.

1. **Admin Settings → External Tools → +(Add Server)**
2. Type: **MCP (Streamable HTTP)** — *not* OpenAPI
3. Server URL: `http://<rig-host>:8077/mcp`
4. Save (restart if prompted)
5. On the model you'll chat with: **Advanced Params → Function Calling = Native**, and
   enable this tool server for the chat.

(If you're on an older Open WebUI without native MCP, bridge via `mcpo`; native is simpler.)

## Connect LibreChat

Streamable HTTP is LibreChat's recommended transport. In `librechat.yaml`:

```yaml
mcpServers:
  evocua_rig:
    type: streamable-http
    url: http://<rig-host>:8077/mcp
    # Optional. Tool calls are fast (job model), so the default is fine; this only caps
    # an individual call, not the rollout.
    timeout: 30000
    serverInstructions: true
```

Restart LibreChat to load it; select **evocua_rig** from the MCP Servers dropdown under
the chat box. (The server must be running before LibreChat starts — it connects at boot.)

## How the orchestrator should use it

Tell the conversational model (system prompt or just by the tool descriptions): to do
something on the computer, call `start_computer_task(goal)`, then **poll**
`get_task_status(job_id)` until `is_terminal`. If status becomes `awaiting_reply`, read
`question`, answer it (from context or by asking the user), and call
`continue_task(job_id, reply)`. When done, **verify** with `get_task_screenshot` if the
result matters — `succeeded` is the model's *self-reported* termination and is **not**
independently checked here.

## Safety

- **Real HID, no undo.** A task drives the actual mouse/keyboard. Run the rig on a
  disposable/sandboxed machine.
- **The instruction is now LLM-generated.** A conversational model (possibly an API model,
  possibly confabulating) issues the goals — a larger trust surface than you typing them.
  Treat anything the rig reads off a web page as a second injection hop into the orchestrator.
- **Single owner, serial.** This server owns the capture card + Pico while it runs; **don't
  run `run_probe.py` against the same rig simultaneously.** Only one task runs at a time; a
  second `start_computer_task` is rejected while one is active.
- **`succeeded` ≠ verified.** No OCR ground-truth here (that's `run_probe`'s microtask gate).
  The model's claim is returned as `answer`; confirm visually if it counts.

## Extension points (deliberately left out of the skeleton)

- **Per-step approval ("confirm destructive").** The MCP analog of `operate.py --confirm`:
  reuse the same `await_reply` pause, but trigger it before *every* action (or before a
  classified-risky one) so the orchestrator/human must approve each step. Heavy-handed
  (a round-trip per action), so it's a toggle to add, not the default.
- **Verify-before-succeed.** Wire `verify.py` (or any checker) into the terminate path so
  `succeeded` means *checked*, not self-reported — when the task has a ground truth.
- **Multi-rig.** The single-slot `JobManager` becomes a pool keyed by rig; `start` picks a
  free rig instead of rejecting.
- **Proper lifespan / persistence.** Jobs live in memory; a long-running deployment would
  expire old jobs and offload screenshots.
