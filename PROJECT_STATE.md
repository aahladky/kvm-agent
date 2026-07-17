# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-06-21 (evening) — consolidated into `kvm_agent/`; the "set default browser" hard-GUI class is SOLVED, plus preemption hardening (UI-TARS grounding mode, vision `ask`-verify, plan-time lint, always-on per-step logging, primitive self-test)._

## 1. What it is
A computer-use agent where **nothing is installed on the target**. A vision model sees the
target's screen over an HDMI capture card, a planner/executive decides actions, and a
**Raspberry Pi Pico 2 W USB-HID injector** physically drives the target's mouse + keyboard over
WiFi. The target sees only a monitor + a USB mouse/keyboard — OS-agnostic and undetectable.

## 2. Current status — **WORKING end to end**
- **Full path is green via Open WebUI.** Goal typed in Open WebUI → `agent_server` → HF planner
  decomposes → executive runs steps (keyboard-first + UI-TARS grounding) → Pico HID on the target
  → verifier checks the screen → progress streams back.
- **Consolidated into one `kvm_agent/` package** (this session). The flat root modules moved into
  the package; the root `.py` modules are now 3-line back-compat shims. Re-verified on the rig:
  `python measure.py --k 10` = **10/10 = 100%** (`runs/measure_20260621_075842`). Behavior is
  identical — `kvm_agent/config.py` defaults equal the old hardcoded literals.
- **Verify hardened** (this session): tesseract is auto-discovered (works off-PATH), `has_text`
  transcribes + substring-matches, `read_number` uses the vision model aimed at the display.
- **Caps-Lock self-correct in firmware** (flashed + live): the Pico reads the target's Caps-Lock
  LED and taps it off before typing, so text types in the requested case (was inverting).
- The Pico `10054` "wedge" bug is FIXED (firmware finite recv timeout, earlier today). WiFi
  power-save **disabled** (`cyw43.PM_DISABLED`).
- **Hard GUI now works: "set default browser to Chrome" is SOLVED** on the rig (Win10 target) —
  the isolated harness runs PASS, Chrome set + vision-verified (`runs/isolate_defbrowser_20260621_091906`).
  Root cause was THREE code/execution bugs, **not planner size** (model set unchanged): (1) UI-TARS,
  used as a grounder via the agentic prompt, emitted `finished()`/scroll on visible targets → fixed
  with the click-only **GROUNDING_DOUBAO** mode; (2) `verify` substring-matched a sentence never on
  screen → fixed with a vision **`ask`** verify; (3) click "ok" was frame-diff "pixels moved" →
  surfaced by per-step logging. Full writeup in the CLAUDE.md "evening" banner.
- **Scroll now works (v5, flashed + verified):** boot.py has a relative Wheel field and code.py's
  `S` handler sends wheel notches, so `r4.scroll()` actually scrolls (selftest: top marker scrolled
  into view, `runs/selftest_20260621_110008`, all four primitives pass). Flashing the new descriptor
  re-enumerates the HID interface — power-cycle the Pico (replug if Windows shows Code 10).

## 3. Architecture (the live system) — code now under `kvm_agent/`
- **PLANNER** (`kvm_agent/orchestration/planner.py`) — decomposes a goal into atomic steps,
  re-plans on failure. Default `HFPlanner` (Qwen3-VL-8B via HF router); also `ClaudePlanner`,
  `LocalPlanner` (the all-local B580 target), `RulePlanner` (deterministic). Entry point `run_goal()`,
  which runs every plan through **`validate_plan()`** (plan-time lint) before execution.
- **EXECUTIVE** (`kvm_agent/orchestration/executive.py`) — runs each step with the right primitive:
  keyboard-first launch/type (Win+R, no grounding) + **UI-TARS stateless grounding** for visual
  clicks, now in **click-only grounding mode** (can't `finished()`/scroll on a visible target).
  `Verifier` (tesseract OCR + `qwen2.5vl` vision) checks the actual screen, not self-report; `verify`
  supports a vision **`ask`** (yes/no) for states not shown as literal text. Every run (unless
  `capture=False`) saves a per-step frame + the grounder's raw output to `runs/<tag>_<time>/`.
- **ENV** (`kvm_agent/hardware/env.py` + `hardware/pico_client.py`) — `Camera` (MSMF HDMI capture)
  + `R4`/`PicoClient` wrapped as a DesktopEnv-shaped `PicoEnv` (drop-in for the upstream OSWorld env).
- **CONFIG** (`kvm_agent/config.py`) — every IP/port/endpoint/model-name/path, env-overridable;
  defaults equal the prior hardcoded literals, so importing through the package is behavior-identical.
- **SERVER** (`kvm_agent/server/app.py`, launched via root `agent_server.py`) — exposes the agent as
  an OpenAI-compatible streaming model (`computer-use-agent`) for Open WebUI; one task at a time.
- **MODELS** (`kvm_agent/models/`) — `uitars.py`, `evocua.py`, `factory.make_agent`. EvoCUA imports
  the **vendored** osworld (`kvm_agent/_vendor/osworld`, 3 files), not the old 55 MB clone.
- **FIRMWARE** (`boot.py` + `code.py`) — the Pico 2 W HID injector (stays at root; deployed to CIRCUITPY).

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
- All of the above are defaults in `kvm_agent/config.py` — override via env (`PICO_IP`, `OLLAMA_HOST`,
  `EXECUTOR_MODEL`, `VERIFIER_MODEL`, `AGENT_PLANNER_MODEL`, `TESSERACT_CMD`, …).

## 5. How to run it
Open `http://192.168.0.155:8080` (Open WebUI) → pick **`computer-use-agent`** → type a plain-English
goal → watch the live stream. **Run `agent_server.py` from the repo root** (so `kvm_agent` is
importable). **Prereqs running:** `agent_server` (`:8088`, real mode) on the desktop; laptop Ollama
with `uitars-q4` + `qwen2.5vl`; HF planner reachable (token + net); capture card on the target; Pico
on the target + on WiFi. One task at a time. (Open WebUI's auto Title/Follow-up/Tag generation is
**disabled** so it doesn't fire background prompts at the rig.)
Re-add the connection if needed: OWUI → Settings → Connections → OpenAI API,
`base_url = http://192.168.0.184:8088/v1`, `api_key =` anything.

## 6. File catalog

### The package — `kvm_agent/` (canonical code)
| Path | Role |
|---|---|
| `config.py` | **All** IPs/ports/endpoints/model-names/paths, env-overridable. |
| `hardware/pico_client.py` | Host-side Pico TCP client (`R4`; `PicoClient` alias). `M/C/R/D/U/K/T/X/S/H`. |
| `hardware/env.py` | `Camera` (MSMF) + `PicoEnv` (DesktopEnv-shaped over the rig). |
| `models/uitars.py` | UI-TARS-1.5-7B adapter (the executor/grounder). |
| `models/evocua.py` | EvoCUA-8B S2 agent (earlier model path); imports the vendored osworld. |
| `models/factory.py` | `make_agent` backend selector. |
| `orchestration/planner.py` | Pluggable planner (Claude/Local/HF/Rule) + `run_goal()`. |
| `orchestration/executive.py` | Hierarchical executive/executor + `Verifier`. **CORE.** |
| `server/app.py` | OpenAI-compatible streaming server for Open WebUI. |
| `llm/ollama.py` | Shared Ollama/OpenAI client helper (staged; wired at the P2 perf step). |
| `_vendor/osworld/mm_agents/` | The 3 upstream files actually imported (utils, prompts, qwen_vl_utils). |

### Root (entry points, firmware, shims, config)
| File | Role |
|---|---|
| `agent_server.py` | **Entry point** — runs the OWUI server (imports `kvm_agent`). |
| `measure.py` | **Entry point** — K-rep reliability measurement. |
| `live_ctl.py` | **Entry point** — interactive REPL rig driver. |
| `boot.py` / `code.py` | **Firmware** — Pico HID injector (deployed to CIRCUITPY; `code.py` = caps-lock + finite recv timeout). |
| `r4_client` / `pico_env` / `uitars_agent` / `evocua_agent` / `cua_agent` / `executive` / `planner` `.py` | 3-line **back-compat shims** → `kvm_agent.*`. Removable once importers repoint. |
| `pyproject.toml`, `.gitignore` | Packaging + ignore (excludes `models/`, `runs/`, `scratch/`, `evocua/`, `__pycache__`). |
| `Modelfile.uitars` | Ollama Modelfile for the UI-TARS executor. |

### Docs
- Root: `CLAUDE.md` (master handoff, read-first) + `PROJECT_STATE.md` (this file).
- `docs/` — all session/findings/plan notes (moved off root), incl.
  `PLAN_2026-06-21_consolidation_and_optimization.md` (the full optimization backlog) +
  `PACKAGING_STATUS_2026-06-21.md`, the `FINDINGS_*`, `SESSION_*`, `README_*`, etc.

### Directories / data
| Dir | Contents |
|---|---|
| `tools/` | Reusable harnesses + diagnostics: `isolate_default_browser.py` (deterministic keyboard-first task harness, per-step frames), `probe_grounding.py` (OFFLINE grounding A/B on a saved frame; `--grounding`), `selftest.py` (primitive capability check), `operate.py`, `run_probe.py`, `eval_harness.py`, `score_batch.py`, `verify.py`, `calibrate_uitars.py`, `evocua_mcp_server.py`, `wol.py`, `pico_*`. |
| `tests/` | `test_uitars_adapter.py` (grow this — pure-logic units are the easy wins). |
| `runs/`, `scratch/`, `models/` | Execution logs / throwaway / 35 GB GGUF quants — **gitignored**. |
| `_archive/` | Superseded code (kept; in git history). _(The 55 MB upstream `evocua/` clone was deleted — vendored to 3 files.)_ |

## 7. Changes this session (2026-06-21)

**Default-browser solved + preemption hardening (evening):**
- Isolated the long-standing "set default browser" failure to THREE code/execution bugs (not
  planner size) and fixed all three with the model set unchanged: UI-TARS click-only
  **GROUNDING_DOUBAO** mode (it was emitting `finished()`/scroll on visible targets), a vision
  **`ask`** verify op (it was substring-matching a sentence never on screen), and per-step logging
  that surfaces a false-positive click. Isolated harness now PASS (Chrome set, vision-verified).
- Added **preemption** layers so this *class* of silent failure is caught, not re-discovered:
  `validate_plan()` plan-time lint (planner.py → wired into `run_goal`), always-on per-step frame +
  raw-grounder logging in `run_plan` (`runs/<tag>_<time>/`; `Executive.capture`), and a primitive
  capability self-test.
- New diagnostic tools in `tools/`: `isolate_default_browser.py`, `probe_grounding.py`, `selftest.py`.
  Documented the **firmware scroll no-op** (`code.py:265` + no wheel byte in the HID report).
- Files touched: `kvm_agent/models/uitars.py` (grounding flag), `kvm_agent/orchestration/{executive,
  planner}.py`, `measure.py` (`capture=False` to keep the benchmark timing baseline clean).
- **Firmware v5 — real scroll** (flashed + verified): boot.py wheel field + code.py `S` handler;
  `selftest.py` now shows all four primitives acting (scroll was a silent no-op before).
- **Re-verify on the rig:** live `run_goal`/Open WebUI end-to-end; `measure.py --k 10` for no
  benchmark regression; `run_plan` per-step capture live (`selftest.py` already passes live).

**Consolidation + hardening (late):**
- Flat root modules → `kvm_agent/` package; root files are now back-compat shims. `measure --k 10` = **10/10**.
- `kvm_agent/config.py` centralizes all IPs/ports/model-names/paths (defaults == old literals; was duplicated across 8–12 files).
- Vendored the 3 used osworld files into `kvm_agent/_vendor`; **deleted the 55 MB `evocua/` clone**; killed the `sys.path` need.
- Verify hardened: tesseract auto-discovery (off-PATH ok), transcribe+substring `has_text`, vision-targeted `read_number`.
- Firmware `code.py`: Caps-Lock LED self-correct + `capslock` named key (flashed + live).
- Repo placed under **git** (baseline → cutover → cleanup on `refactor/packaging`); `.gitignore` added; notes moved to `docs/`.

**Earlier today (pico wedge fix):**
- `code.py` `CONN_TIMEOUT = 45` + finite per-connection recv timeout (anti-wedge). `r4_client` retries 2→4.
  `agent_server` pre-connect removed. See `docs/FINDINGS_2026-06-21_pico_wedge_fix.md`.

## 8. Known issues / next steps
- **No per-command ACK** (R1 in the plan) — a half-open that lands mid-rollout costs that one rollout
  (recovers on the next). Add a 1-byte ack to the protocol. Highest-value robustness item.
- **Wait-for-stable** (R2) — replace the ~20 fixed `time.sleep`s with frame-stability polling (faster
  *and* more robust); the `_frame_diff` primitive already exists.
- **Shims removable** — repoint the ~8 importers (`agent_server`, `measure`, `live_ctl`, `tools/*`) to
  `kvm_agent.*` and delete the 7 root shims. Deliberately kept for now (zero runtime cost, reversible).
- **Hard GUI tasks** — the default-browser class is now SOLVED (grounding mode + `ask`-verify); the
  general lever going forward is the preemption layer, not a bigger planner.
- **Preemption hardening (this session)** — catch silent failures by construction / at plan time /
  loudly: UI-TARS click-only **grounding mode**; vision **`ask`** verify op; **`validate_plan`**
  plan-time lint; **always-on per-step frame + raw-grounder logging** in `run_plan`; **`tools/selftest.py`**
  primitive capability check.
- **Scroll op (now unblocked)** — the firmware wheel works (v5, verified); add a `scroll` op to the
  plan schema + executive (e.g. `{"op":"scroll","dir":"down","ticks":3}` or a `scroll_until(target)`
  helper) so plans can reach below-the-fold controls instead of relying on maximize/keyboard.
- **Optional** — a Win10 default-apps idiom in the planner (launch `ms-settings:defaultapps` → click
  the current browser tile → click the target browser → `ask`-verify) to make that plan deterministic.
- **Future:** stand up `LocalPlanner` on the desktop B580 (all-local planner path) — only a `base_url`
  change from `HFPlanner`.
- Full optimization backlog (R1–R8 robustness, P1–P6 model-preserving perf):
  `docs/PLAN_2026-06-21_consolidation_and_optimization.md`.
