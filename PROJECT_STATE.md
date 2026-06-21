# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-06-21._

## 1. What it is
A computer-use agent where **nothing is installed on the target**. A vision model sees the
target's screen over an HDMI capture card, a planner/executive decides actions, and a
**Raspberry Pi Pico 2 W USB-HID injector** physically drives the target's mouse + keyboard over
WiFi. The target sees only a monitor + a USB mouse/keyboard — OS-agnostic and undetectable.

## 2. Current status — **WORKING end to end**
- **Full path is green via Open WebUI.** Goal typed in Open WebUI → `agent_server` → HF planner
  decomposes → executive runs steps (keyboard-first + UI-TARS grounding) → Pico HID on the target
  → `qwen2.5vl` verifier checks the screen → progress streams back. Verified 2026-06-21:
  "Open Notepad and type: hello from open-webui" → **done in 8.6s, 0 re-plans, `verify: true`**.
- **The Pico `10054` "wedge" bug is FIXED** (today). It was a firmware `conn.settimeout(None)`
  that wedged the serve loop on any half-open connection — root-caused, patched, flashed, and
  confirmed under live Open WebUI load (zero resets across dozens of runs). Full writeup:
  `FINDINGS_2026-06-21_pico_wedge_fix.md`.
- WiFi power-save confirmed **disabled** on the device (`cyw43.PM_DISABLED` works on CP 10.2.1).
- Real multi-step GUI goals execute and ground correctly. Genuinely hard tasks (e.g. Win11
  "set default browser") still fail on **task difficulty / verify**, not infrastructure.

## 3. Architecture (the live system)
- **PLANNER** (`planner.py`) — decomposes a goal into atomic steps, re-plans on failure. Default
  `HFPlanner` (Qwen3-VL-8B via HF router). Also `ClaudePlanner`, `LocalPlanner` (the all-local
  B580 target), `RulePlanner` (deterministic fallback). Entry point `run_goal()`.
- **EXECUTIVE** (`executive.py`) — runs each step with the right primitive: keyboard-first
  launch/type (Win+R, no grounding) and **UI-TARS stateless grounding** for visual clicks.
  `Verifier` (qwen2.5vl) checks the actual screen, not self-report.
- **ENV** (`pico_env.py`) — `Camera` (MSMF HDMI capture) + `R4` Pico client wrapped as a
  DesktopEnv-shaped `PicoEnv` (drop-in for the upstream OSWorld env).
- **SERVER** (`agent_server.py`) — exposes the agent as an OpenAI-compatible streaming model
  (`computer-use-agent`) for Open WebUI; one task at a time; `--mock` for wiring tests.
- **FIRMWARE** (`boot.py` + `code.py`) — the Pico 2 W HID injector.

## 4. Hardware topology + addresses
| Role | Machine | Address |
|---|---|---|
| Orchestrator (agent_server + HDMI capture card) | Desktop — Win11, i7-14700K, Arc B580 | `192.168.0.184`, agent_server `:8088` |
| Models + front end | Laptop — RTX 4080 mobile (12 GB) | `192.168.0.155`; Ollama `:11434`, Open WebUI `:8080` |
| HID injector | Pico 2 W (RP2350, CircuitPython 10.2.1) | `192.168.0.183:8000` (DHCP-reserved) |
| Target | the machine the Pico's USB drives + the capture card watches | — |

- Ollama models (on the laptop): **`uitars-q4`** (executor/grounder), **`qwen2.5vl:7b`** (verifier).
- Planner: **`Qwen/Qwen3-VL-8B-Instruct`** via HF router (token auto-resolved).
- Pico when plugged into the orchestrator: serial console = **`COM7`**, CIRCUITPY drive = **`I:`**.

## 5. How to run it
Open `http://192.168.0.155:8080` (Open WebUI) → pick **`computer-use-agent`** → type a plain-English
goal → watch the live stream. **Prereqs running:** `agent_server` (`:8088`, real mode) on the desktop;
laptop Ollama with `uitars-q4` + `qwen2.5vl`; HF planner reachable (token + net); capture card on the
target; Pico on the target + on WiFi. One task at a time. (Open WebUI's auto Title/Follow-up/Tag
generation is **disabled** so it doesn't fire background prompts at the rig.)
Re-add the connection if needed: OWUI → Settings → Connections → OpenAI API,
`base_url = http://192.168.0.184:8088/v1`, `api_key =` anything.

## 6. File catalog

### Live system (repo root)
| File | Role |
|---|---|
| `planner.py` | Pluggable planner layer (Claude / Local / HF / Rule) + `run_goal()`. |
| `executive.py` | Hierarchical executive/executor + `Verifier` (qwen2.5vl). **CORE.** |
| `pico_env.py` | `Camera` (MSMF) + `R4` + `PicoEnv` (DesktopEnv-shaped over the rig). |
| `r4_client.py` | Host-side Pico TCP client; `M/C/R/D/U/K/T/X/S/H` protocol. _(edited today: 4 retries)_ |
| `agent_server.py` | OpenAI-compatible streaming server for Open WebUI. _(edited today: pre-check removed)_ |
| `uitars_agent.py` | UI-TARS-1.5-7B adapter (the executor/grounder model). |
| `evocua_agent.py` | EvoCUA-8B S2 agent — the earlier model path; matches upstream protocol. |
| `cua_agent.py` | Backend selector for the rig's agents. |
| `live_ctl.py` | Persistent interactive REPL controller for the rig (dev driver). |
| `measure.py` | Honest reliability measurement (K-rep success rates, not anecdotes). |
| `boot.py` | **Firmware**: Pico HID descriptor v4 (Report-ID-2 abs mouse + keyboard). |
| `code.py` | **Firmware**: Pico WiFi serve loop + HID exec. _(FIXED today — finite recv timeout)_ |
| `Modelfile.uitars` | Ollama Modelfile for the UI-TARS executor. |

### Docs (repo root)
- `CLAUDE.md` — master handoff / session log (43 KB). Read-first.
- `PROJECT_STATE.md` — this file.
- `FINDINGS_2026-06-21_pico_wedge_fix.md` — **today**: the `10054` wedge root cause + fix.
- `FINDINGS_2026-06-20_executive_architecture.md` — the planner/executive/verifier split (10/10).
- `FINDINGS_2026-06-20_uitars_q4.md` / `_uitars_Q8.md` / `_uitars_FIX_live.md` — UI-TARS bring-up.
- `FINDINGS_2026-06-19_flail_rootcause.md`, `FINDINGS_2026-06-18_rootcause.md` — earlier diagnoses.
- `PLAN_2026-06-19_model_bakeoff.md`, `SESSION_2026-06-21.md` — plan + prior session note.
- `README_openwebui.md` — Open WebUI wiring. `README_evocua_mcp.md` — MCP server.
- `UITARS_INTEGRATION.md`, `DEMOS.md` — integration notes + demo goals.

### Directories
| Dir | Contents |
|---|---|
| `evocua/` (550) | Upstream EvoCUA + OSWorld `desktop_env` / `mm_agents` package — the reference the adapters match. |
| `tools/` (13) | Reusable harnesses + diagnostics: `operate.py` (interactive operator), `run_probe.py`, `measure`/`score_batch`/`eval_harness`, `verify.py`, `calibrate_uitars.py`, `evocua_mcp_server.py`, `wol.py`, `pico_serial_log.py`, `pico_diag_windows.py`, `demo_*`, + today's `pico_console_*.log`. |
| `runs/` (684) | Execution logs — 69 run-dirs + 29 `goal_*.json` (plan/step/status records). |
| `scratch/` (157) | Throwaway: today's diagnostics (`_pico_diag/idle/load/precheck/recover`, `_flash_and_capture`, `_serial_demo`, `_read_serial`, `_owui_smoke`, `_planner_check`, `trigger_goal`) + logs + frames. |
| `models/` (12) | Desktop-side GGUF quants (`evocua-8b` f16/q5/q8) + build/ssh logs. _Operational models run on the laptop Ollama, not here._ |
| `_archive/` (69) | Superseded code + `firmware_old/` (boot_v2/code_v2) + `scratch_probes/` + loose diag scripts. |
| `probes/` (4), `tests/` (1) | Small format/size probes; `test_uitars_adapter.py`. |
| `__pycache__/` (27) | Bytecode cache (ignorable). |

## 7. Changes made this session (2026-06-21)
- `code.py` — **`CONN_TIMEOUT = 45`** + finite per-connection recv timeout (the anti-wedge fix). Flashed + verified.
- `r4_client.py` — send retries 2 → 4 with backoff.
- `agent_server.py` — removed the wedging "check Pico injector" pre-connect.
- Added `FINDINGS_2026-06-21_pico_wedge_fix.md` + this `PROJECT_STATE.md`.
- Disabled Open WebUI's auto Title/Follow-up/Tag generation (they were executing as rig goals).

## 8. Known issues / next steps
- **No per-command ACK.** A half-open that lands *mid-rollout* can still cost that one rollout
  (recovered on the next — no power-cycle). If it matters, add a 1-byte ack to the protocol.
- **Stale `pico_serial_log.py` stuck on the target's COM3** — kill it (hogs the port, spews errors).
- **Hard GUI tasks** (Win11 default-browser, dense targets) fail on grounding/verify — model/task
  difficulty, not infra. UI-TARS grounding is the lever.
- **Optional:** a `### Task:` short-circuit guard in `agent_server` as a backstop against OWUI
  utility prompts (zero VRAM; currently moot since those are disabled).
- **Future:** stand up `LocalPlanner` on the desktop B580 (all-local planner path) — only a
  `base_url` change from `HFPlanner`.
- **Hygiene:** `scratch/` (157) and `runs/` (684) are accumulating — periodic prune.
