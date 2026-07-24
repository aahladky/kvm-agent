# Driving the rig from Open WebUI

`agent_server.py` exposes the computer-use agent as an **OpenAI-compatible model**
(`computer-use-agent`). Open WebUI connects to it like any OpenAI endpoint; you pick the
model and type a **goal**, and the server runs `planner.run_goal(executive)` on the
physical rig, **streaming live progress** back into the chat.

```
Open WebUI (laptop 192.168.0.155:8080)
   └─ OpenAI connection ─▶ agent_server.py  (desktop 192.168.0.184:8088)
                               ├─ PLANNER  = HFPlanner (Qwen3-VL-8B via HF router)   ← cloud, swappable
                               ├─ EXECUTOR = UI-TARS on laptop Ollama
                               ├─ VERIFIER = qwen2.5vl on laptop Ollama
                               └─ HID + capture = this desktop (Pico + capture card)
```

## 1. Run the server (on the desktop — it holds the rig)
```
python C:\Dev\vllm\agent_server.py --mock     # safe wiring test, no rig
python C:\Dev\vllm\agent_server.py            # real rig (boots camera+Pico on first task)
```
Verified working locally: `/health`, `/v1/models`, streaming `/v1/chat/completions`.

## 2. Add it in Open WebUI (laptop)
Settings → **Admin Settings → Connections → OpenAI API → +**
- **URL:** `http://192.168.0.184:8088/v1`
- **Key:** anything (e.g. `x`)

Save. `computer-use-agent` now appears in the model dropdown. Start a new chat, select it,
and type a goal, e.g. *"Open Notepad and type milk, eggs, and bread; then open Calculator
and compute 42 + 17."* Progress streams in as it runs.

## 3. If Open WebUI can't reach it (connection error)
The desktop firewall is likely blocking inbound 8088. In an **elevated** PowerShell/cmd on
the desktop:
```
netsh advfirewall firewall add rule name="agent_server_8088" dir=in action=allow protocol=TCP localport=8088
```
(Confirm the server is listening: `netstat -an | findstr :8088` → `0.0.0.0:8088 LISTENING`.)

## 4. Choosing the planner (env vars, before launching the server)
| var | default | notes |
|---|---|---|
| `AGENT_PLANNER` | `hf` | `hf` \| `claude` \| `rule` |
| `AGENT_PLANNER_MODEL` | `Qwen/Qwen3-VL-8B-Instruct` | any served HF model; bump to `Qwen/Qwen2.5-VL-72B-Instruct` or `Qwen/Qwen3-VL-235B-A22B-Instruct` for sharper plans |
| `AGENT_SEND_IMAGE` | `1` | `0` for a text-only planner |
| `EXECUTOR_MODEL` | `uitars-q4` | UI-TARS on Ollama |
| `VERIFIER_MODEL` | `qwen2.5vl:7b` | screen verifier on Ollama |

Example (cmd): `set "AGENT_PLANNER_MODEL=Qwen/Qwen2.5-VL-72B-Instruct" && python C:\Dev\vllm\agent_server.py`

When the all-local B580 planner is ready, set `AGENT_PLANNER_MODEL` + point `LocalPlanner`
at it — no other change.

## Notes
- **One task at a time** (single rig). A second concurrent goal gets "rig is busy."
- The server keeps the camera+Pico open for its lifetime; stop it (Ctrl+C) to release them.
- A multi-minute task streams progress the whole time, so the chat won't look hung.
