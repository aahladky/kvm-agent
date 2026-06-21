# Plan — Consolidation + Optimization Review

_Authored 2026-06-21. Scope: (1) consolidate the loose scripts into one contained package,
(2) optimize performance + ability **without changing the models** (uitars-q4 / qwen2.5vl /
Qwen3-VL-8B stay), (3) other code improvements. Per request this is **review + proposed
layout only** — no files moved. Emphasis: **packaging/structure** and **robustness/ability**._

> **Caveat I can't escape:** I can't reach the rig from here (Pico, capture card, laptop
> Ollama are on your LAN). Every claim below is from static reading of the source; anything
> touching the hardware loop has to be re-verified on the desktop. All file:line refs were
> checked against the current tree.

---

## 0. TL;DR

The live system is ~10 clean, well-commented modules. The problem is **everything around
them**: no package boundary, so 7 files carry `sys.path` hacks, the same Ollama endpoint is
re-declared in 8 files, LAN IPs in 10, model names in 12, and the 55 MB / 522-file `evocua/`
upstream clone is on the tree to provide exactly **3 imported files**. The repo **isn't under
git** yet sits on **35 GB** of model blobs. None of that is a code-quality problem in the
modules — it's a *containment* problem, which is exactly what you flagged.

Recommended order (each step independently testable, nothing stacked):

1. **`git init` + `.gitignore` first** — safety net before any move (§5).
2. **`config.py`** — collapse the IP/port/model/path duplication into one module (§2.3). Highest
   value-to-risk ratio; touches no logic.
3. **Package the modules** into `kvm_agent/` with import shims so the running server never
   breaks mid-migration (§2.1–2.2, §6).
4. **Vendor-trim `evocua/`** to the 3 files actually imported → deletes all 7 `sys.path` hacks
   (§2.4).
5. Then the **robustness/ability** items (§3) and the **model-preserving perf** items (§4).

---

## 1. Current state (verified)

| Symptom | Count / size | Where |
|---|---|---|
| `sys.path.insert/append` hacks | **7 files** | `agent_server.py:29`, `tools/operate.py:37`, `tools/run_probe.py:25`, `tools/calibrate_uitars.py:36`, `tools/evocua_mcp_server.py:171-175`, `tools/demo_mcp_mock.py:23`, `tools/demo_parser_fix.py:21-22` |
| `OPENAI_BASE_URL/KEY` re-declared | **8 files** | `agent_server`, `executive`, `live_ctl`, `tools/operate`, `tools/calibrate_uitars`, `tools/evocua_mcp_server` (+2 mock variants) |
| Hardcoded LAN IPs (`192.168.0.x`) | **10 files** | incl. `r4_client.py:18`, `executive.py:36/39`, `uitars_agent.py:207`, `agent_server.py:30` |
| Hardcoded model names | **~12 files** | `uitars-q4`, `qwen2.5vl:7b`, `evocua-8b-q5-clean`, `Qwen/Qwen3-VL-8B-Instruct` |
| `evocua/` clone | **522 files / 55 MB** | host runtime imports **3**: `mm_agents/evocua/utils.py`, `mm_agents/evocua/prompts.py`, `mm_agents/utils/qwen_vl_utils.py` |
| Root under git? | **No** | `evocua/` has its own nested `.git` (a second problem — §2.4) |
| Untracked heavy dirs | `models/` **35 GB**, `runs/` 883 MB, `scratch/` 216 MB | must be gitignored before any `git add` |
| Fixed `time.sleep` in hot paths | executive **12**, pico_env **8** | §3, §4 |

Four structural problems fall out of that table:

**(a) No package boundary.** Because the modules sit at repo root and the patched
`evocua_agent.py` needs upstream's top-level `mm_agents` package, every entry point has to
hand-edit `sys.path` in the right *order* (root ahead of `evocua/`). That ordering dependency
is the root cause of the `# APPEND, not insert(0)` comments in `operate.py:37` and
`run_probe.py:25`. A real package makes the whole class of bug impossible.

**(b) Config is copy-pasted, not centralized.** The same `192.168.0.155:11434` Ollama URL,
the same `uitars-q4`/`qwen2.5vl:7b` names, and `C:\Dev\vllm` absolute paths are restated in
file after file. Moving the laptop to a new IP, or renaming a quant, is currently a
find-and-replace across ~10 files — error-prone, and a real source of "why is it hitting the
old endpoint" bugs. (Already a minor drift: `planner.py:158` docstring says
`Qwen/Qwen2.5-VL-7B-Instruct` while the server default is `Qwen/Qwen3-VL-8B-Instruct`.)

**(c) The `evocua/` clone is 99.4% dead weight on the host.** Nothing imports
`evocua/desktop_env` (confirmed — `pico_env.py` is the *replacement* for it and imports only
`r4_client`). The host needs `utils.py`, `prompts.py`, and the `smart_resize` they pull from
`qwen_vl_utils.py`. The other 519 files (providers for aws/azure/gcp/docker/vmware, ~430
`evaluation_examples/*.json`, assets) are reference material that belongs in the upstream
GitHub repo, not your working tree — and its nested `.git` will fight a top-level `git init`.

**(d) Stale shadowing comments.** `operate.py:39/44` and `run_probe.py` still warn that
`evocua/evocua_agent.py` "is shadowed (1/5 formats)". **That file no longer exists** — only the
patched root `evocua_agent.py` remains. The comments now describe a hazard that's gone, which
is worse than no comment: the next person preserves a `sys.path` ordering for a reason that
expired.

None of this implicates the model loop, your firmware fixes, or the planner/executive split —
those are in good shape. This is packaging and config hygiene.

---

## 2. Proposed layout (the main ask)

### 2.1 Target tree

A single importable package, `kvm_agent/`, with the modules grouped by role. Firmware stays
**out** of the package (it runs on the Pico, not the host). Data/junk moves under `data/`.

```
vllm/                              # repo root → becomes the git repo
├─ pyproject.toml                  # deps + console_scripts (no more sys.path hacks)
├─ .gitignore                      # data/, __pycache__/, *.gguf, _dbg/
├─ README.md                       # the "How to run" from PROJECT_STATE §5, consolidated
├─ config.toml.example             # documents every overridable setting (§2.3)
│
├─ kvm_agent/                      # ── THE PACKAGE ──
│  ├─ __init__.py
│  ├─ config.py                    # ← all IPs/ports/models/paths, env-overridable (§2.3)
│  │
│  ├─ hardware/
│  │  ├─ pico_client.py            # ← r4_client.py        (R4 → PicoClient, keep `R4 = PicoClient` alias)
│  │  ├─ camera.py                 # ← Camera              (split out of pico_env.py)
│  │  └─ env.py                    # ← PicoEnv + PicoController + PicoPyAutoGUI (rest of pico_env.py)
│  │
│  ├─ models/
│  │  ├─ base.py                   # the agent contract (reset/predict/last_answer) as a Protocol
│  │  ├─ uitars.py                 # ← uitars_agent.py
│  │  ├─ evocua.py                 # ← evocua_agent.py     (the patched copy; the ONLY one)
│  │  └─ factory.py                # ← cua_agent.make_agent
│  │
│  ├─ orchestration/
│  │  ├─ planner.py                # ← planner.py          (Planner/Claude/Local/HF/Rule + run_goal)
│  │  ├─ executive.py              # ← executive.py        (Executive)
│  │  └─ verifier.py               # ← Verifier            (split out of executive.py)
│  │
│  ├─ llm/
│  │  └─ ollama.py                 # ONE reusable client for Ollama /api/generate + OpenAI /v1 (§4)
│  │
│  ├─ server/
│  │  └─ app.py                    # ← agent_server.py
│  │
│  └─ _vendor/osworld/             # ← the 3 trimmed upstream files (§2.4), explicit namespace
│     └─ mm_agents/{evocua/{utils,prompts}.py, utils/qwen_vl_utils.py, __init__.py…}
│
├─ firmware/                       # device-side; NOT imported by the host
│  ├─ boot.py                      # ← boot.py
│  └─ code.py                      # ← code.py
│
├─ cli/                            # thin entry points; `python -m kvm_agent...` or console_scripts
│  ├─ serve.py        (agent_server runner)
│  ├─ operate.py      ├─ run_probe.py   ├─ measure.py   ├─ live_ctl.py
│  └─ eval_harness.py └─ score_batch.py └─ calibrate_uitars.py
│
├─ tools/                          # pure diagnostics: wol.py, pico_diag_windows.py, pico_serial_log.py
├─ tests/                          # grow this (§5)
├─ docs/                           # ← the 15 root *.md findings/session/plan files
└─ data/                           # all gitignored
   ├─ runs/   ├─ scratch/   └─ models/      # ← 35 GB stays here, never in git
```

Naming is a suggestion — `kvm_agent` reads well and avoids colliding with the model name
`evocua` (part of the current confusion). Pick whatever you like; the *grouping* is the point.

### 2.2 File migration map

| Today (root) | Goes to | Notes |
|---|---|---|
| `r4_client.py` | `kvm_agent/hardware/pico_client.py` | rename class `R4`→`PicoClient`; keep `R4 = PicoClient` so callers don't break |
| `pico_env.py` → `Camera` | `kvm_agent/hardware/camera.py` | clean split; `Camera` has no Pico dependency |
| `pico_env.py` → rest | `kvm_agent/hardware/env.py` | `PicoEnv`, `PicoController`, `PicoPyAutoGUI` |
| `uitars_agent.py` | `kvm_agent/models/uitars.py` | |
| `evocua_agent.py` | `kvm_agent/models/evocua.py` | the patched copy is now canonical and unambiguous |
| `cua_agent.py` | `kvm_agent/models/factory.py` | |
| `planner.py` | `kvm_agent/orchestration/planner.py` | |
| `executive.py` → `Executive` | `kvm_agent/orchestration/executive.py` | |
| `executive.py` → `Verifier` | `kvm_agent/orchestration/verifier.py` | it's a distinct responsibility; ~70 lines |
| `agent_server.py` | `kvm_agent/server/app.py` | + `cli/serve.py` runner |
| `boot.py`, `code.py` | `firmware/` | **not** under the package |
| `tools/operate.py`, `run_probe.py`, `measure.py`, `live_ctl.py`, `eval_harness.py`, `score_batch.py`, `calibrate_uitars.py` | `cli/` | become thin `argparse` wrappers over package imports |
| `tools/wol.py`, `pico_diag_windows.py`, `pico_serial_log.py` | `tools/` (stay) | pure diagnostics |
| `evocua/` (3 used files) | `kvm_agent/_vendor/osworld/` | §2.4 |
| `evocua/` (rest) | **delete from tree** | it's in upstream GitHub; keep a URL+SHA in `docs/` |
| 15 root `*.md` | `docs/` | keep `README.md` + `PROJECT_STATE.md` at root if you like |
| `runs/`, `scratch/`, `models/` | `data/` | gitignored |
| `_archive/`, `probes/`, `__pycache__/` | delete | superseded; recoverable from git once it exists |

Interactive muscle-memory (`import measure`, `from live_ctl import *`) changes to
`from kvm_agent... import` or `python -m`. If that's annoying, leave 3-line shim files at root
(`measure.py` → `from kvm_agent.dev.measure import *`) during the transition.

### 2.3 `config.py` — the single source of truth

One module, env-overridable, imported everywhere a literal IP/port/model/path lives today.
Sketch:

```python
# kvm_agent/config.py
import os
from dataclasses import dataclass

def _env(k, d): return os.environ.get(k, d)

@dataclass(frozen=True)
class Config:
    # --- hardware ---
    pico_ip:   str = _env("PICO_IP", "192.168.0.183")
    pico_port: int = int(_env("PICO_PORT", "8000"))
    cam_index: int = int(_env("CAM_INDEX", "0"))
    screen_w:  int = int(_env("SCREEN_W", "1920"))
    screen_h:  int = int(_env("SCREEN_H", "1080"))
    # --- model endpoints (NOT the models — those are named, not swapped) ---
    ollama_base:  str = _env("OLLAMA_HOST", "http://192.168.0.155:11434")
    openai_base:  str = _env("OPENAI_BASE_URL", "http://192.168.0.155:11434/v1")
    openai_key:   str = _env("OPENAI_API_KEY", "ollama")
    # --- model names (unchanged set; centralized) ---
    executor_model: str = _env("EXECUTOR_MODEL", "uitars-q4")
    verifier_model: str = _env("VERIFIER_MODEL", "qwen2.5vl:7b")
    planner_model:  str = _env("AGENT_PLANNER_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
    # --- paths ---
    repo_root: str = _env("KVM_ROOT", os.path.dirname(os.path.dirname(__file__)))
    runs_dir:  str = _env("RUNS_DIR", "")  # default derived from repo_root

CFG = Config()
```

Then `r4_client` becomes `PicoClient(ip=CFG.pico_ip, port=CFG.pico_port)`, the executive's
`os.environ.setdefault(...)` block disappears (the `llm/ollama.py` client reads `CFG`), and
`agent_server`'s `sys.path.insert(0, r"C:\Dev\vllm")` is gone because it's a package. Net:
the 8-file endpoint duplication and the 10-file IP sprawl collapse to one file, and relocating
the laptop or Pico becomes one env var.

### 2.4 The `evocua/` fix (kills all 7 `sys.path` hacks)

The host imports three pure-Python files. Recommended: **vendor-trim**, don't keep the clone.

1. Copy `mm_agents/evocua/utils.py`, `mm_agents/evocua/prompts.py`,
   `mm_agents/utils/qwen_vl_utils.py` (plus the `__init__.py` files) into
   `kvm_agent/_vendor/osworld/mm_agents/…`.
2. Rewrite **three import lines** to the explicit namespace:
   - `kvm_agent/models/evocua.py` (was `evocua_agent.py:13,21`):
     `from kvm_agent._vendor.osworld.mm_agents.evocua.utils import …`
     `from kvm_agent._vendor.osworld.mm_agents.evocua.prompts import …`
   - `…/utils.py:9`: `from kvm_agent._vendor.osworld.mm_agents.utils.qwen_vl_utils import smart_resize`
3. Delete `evocua/` (and its nested `.git`). Record the upstream URL + commit SHA in
   `docs/vendored_osworld.md` so the provenance is preserved.

That's **3 edited imports** in exchange for deleting **7 `sys.path` manipulations**, 55 MB,
522 files, and a nested git repo. If you'd rather not vendor, the alternatives are (b) `pip
install -e` the upstream as a real dependency, or (c) a proper git submodule — both heavier
than trimming three files you've already pinned. I'd vendor-trim.

---

## 3. Robustness & ability (focus area)

These change *behavior reliability*, not the models. Ordered by payoff. Items marked ⚠ are
the ones I'd do first.

| # | Issue | Where | Fix |
|---|---|---|---|
| R1 ⚠ | **No per-command ACK** — a half-open socket mid-rollout silently no-ops every remaining action | firmware `code.py`; `r4_client.py:80-81` (`recv` just times out) | 1-byte `OK` reply per command; `_send` waits for it instead of sleeping out the 0.25 s timeout. Turns "silent dead rollout" into a detected, retried command. Already on your radar (PROJECT_STATE §8). |
| R2 ⚠ | **Fixed `time.sleep` instead of waiting for the screen** — 20 sleeps across executive+env; too long = slow, too short = flaky on a loaded target | `executive.launch:203-205` (1.2+0.4+2.5 s), `run_plan` settles, `pico_env._settle` | Replace constants with `wait_until_stable(timeout)`: poll frames, return as soon as the frame-diff drops below noise for ~2 reads. The `_frame_diff` primitive already exists (`executive.py:147`). Faster *and* more robust — the central lever for both goals. |
| R3 ⚠ | **Verifier model-swap thrash** degrades reliability, not just speed | `executive.Verifier._vision` calls `qwen2.5vl` while executor is `uitars-q4` on one 12 GB GPU; `reset_clean` calls vision every iteration (`:297`) | Prefer the deterministic OCR path (already preferred when `pytesseract` present — confirm tesseract.exe is installed on the desktop; `Verifier.__init__:53-60` silently falls back to the swapping vision path if not). Independently, test keeping both models resident (§4 P4). |
| R4 | **`reset_clean` is the slowest, least-bounded step** — up to `max_close` iterations, each Alt+Space→c→Alt+N (≈2.9 s of sleeps) + a vision call | `executive.py:286-316` | Add an early "already clear" fast check (you have `desktop_is_clear`), shorten settles via R2, and cap the vision calls. This runs before *every* measure rep. |
| R5 | **`exec()` of model-generated strings** is the design, but the namespace is broad | `pico_env.PicoController.execute_python_command:167` (globals expose `pyautogui`+`time`) | Acceptable for a sandbox rig; tighten by validating the action string against an allowlist of `pyautogui.<fn>(` calls before `exec`. Cheap defense against a malformed/hallucinated action doing something odd. |
| R6 | **Single hardcoded `reset_coord=(534,630)`** assumes a calculator AC button position | `pico_env.PicoEnv.__init__:197` | Fine for the benchmark; document it as benchmark-only (the comment at `observe()` half-says this) so it isn't mistaken for a general reset. |
| R7 | **Bare `except Exception: pass`** hides real failures (capture decode, log write, OCR) | e.g. `executive.py:380-381`, `pico_env.py:205-206` | Keep fail-open where intended, but log at `debug` so a silently-swallowed error is recoverable from the run log. Ties into §5 logging. |
| R8 | **`keyDown`/`keyUp` are no-ops** → no held modifiers (e.g. shift-drag, ctrl-hold-click) | `pico_env.py:137-144` | Capability gap, not a bug. If a future task needs it, the firmware already has `D`/`U` for mouse; add a held-key primitive (`code.py` press-and-hold) and wire it. Note it as a known limit. |

The two highest-leverage are **R1** (ACK) and **R2** (wait-for-stable). Together they convert
the loop from "fixed-time, fail-silent" to "event-driven, fail-detected" — which is the single
biggest reliability upgrade available without touching a model.

---

## 4. Performance levers (models unchanged)

Lighter section per your priority, but these are real and model-agnostic:

| # | Lever | Where | Expected effect |
|---|---|---|---|
| P1 | **Wait-for-stable instead of fixed sleeps** (same as R2) | executive + pico_env | Biggest wall-clock win; pure upside |
| P2 | **Reuse one HTTP client** — today a fresh `openai.OpenAI(...)` is built per call, and there are 3 separate paths to the same Ollama | `uitars_agent.py:209`, `planner.py:135/142`, `executive._vision` uses raw `urllib` | `kvm_agent/llm/ollama.py`: one persistent client/session (keep-alive). Saves connection setup on every grounding/verify/plan call. |
| P3 | **JPEG (q≈85) instead of PNG** for the frames sent to the models | `pico_env.Camera.png_bytes:57` feeds every model call | 1080p PNG is ~2–3 MB; JPEG is ~150–300 KB → far less to base64 + ship to the laptop each step. Models smart-resize anyway. **Test grounding parity first** (small-target risk), but this is a big network/latency cut if it holds. |
| P4 | **Keep executor + verifier resident** | Ollama serving config on the laptop, not code | `OLLAMA_MAX_LOADED_MODELS=2` + `keep_alive` so `uitars-q4`↔`qwen2.5vl` don't evict each other. **Verify VRAM**: both on a 12 GB card is tight — measure before committing. Config change, not a model change. |
| P5 | **Throttle the camera read loop** | `pico_env.Camera._loop:48-52` (`while: cap.read()` with no sleep) | Busy-loop pegs a core for no benefit at 30 fps capture; a 5–10 ms sleep frees CPU for the encode/HTTP path. |
| P6 | **Stop re-grabbing+re-encoding the same frame** | `executive` re-`observe()`s before **and** after each action (`launch:202-206`, `click_target:231-236`); each call is a fresh PNG encode | Thread the "after" frame into the next step instead of re-grabbing. Minor next to P1–P3 but free. |

---

## 5. Other improvements

**Git first — this is the real risk.** The root isn't a repo, so there's no undo for the
migration you're about to do. Before moving anything: `git init`, add a `.gitignore`
(`data/`, `**/__pycache__/`, `*.gguf`, `_dbg/`, `.venv/`), commit the current working tree as
the baseline. **Critical:** the `.gitignore` must exclude `models/` (35 GB) *before* the first
`git add`, or git will try to swallow the blobs. Also delete `evocua/.git` (nested repo) as
part of §2.4 or it becomes an accidental submodule.

**`pyproject.toml`** — declare deps (`opencv-python`, `pillow`, `openai`, `anthropic`,
`backoff`, `fastapi`, `uvicorn`, `ui-tars`, `pytesseract`, `numpy`) and `console_scripts`
(`kvm-serve`, `kvm-operate`, `kvm-measure`). This is what lets `cli/` import the package with
zero `sys.path` lines.

**Docs consolidation** — 15 `*.md` at root (160 KB) is a lot of session archaeology.
`CLAUDE.md` (43 KB) and the `FINDINGS_*` files are genuinely valuable history; move them to
`docs/` and keep `README.md` (how-to-run) + `PROJECT_STATE.md` (current state) at root. The
`CLAUDE.md` header even flags that the EvoCUA/quant saga is superseded — mark those sections
clearly historical so they don't mislead.

**Typed plan schema** — plan steps are raw dicts validated ad hoc inside `run_plan`
(`executive.py:326-357`) and produced by every planner + `RulePlanner`. A small `Step`
dataclass / `TypedDict` (or a `validate_plan()` that the planner output passes through) would
catch a malformed `{"op":"clik"}` at plan time instead of as a mid-rollout `unknown op`. The
schema is already documented in `planner.py:23-34` — formalize it.

**Logging instead of `print`** — the server and loops print to stdout (`agent_server`,
`pico_env:218/274`, `live_ctl`). A `logging` setup with a per-run file handler under
`data/runs/<tag>/` makes failures diagnosable after the fact and lets R7's swallowed
exceptions surface at `debug`.

**FastAPI modernization** — `agent_server.py:190` uses `@app.on_event("shutdown")`, deprecated
in favor of the `lifespan` context manager. Trivial, and removes a deprecation warning on
every boot.

**Tests** — only `tests/test_uitars_adapter.py` exists. The pure-logic, no-hardware functions
are easy wins and exactly where silent regressions hide: `planner._extract_json` (fence/prose
tolerance), `RulePlanner.decompose`, `r4_client.norm_key` + `combo`/`type` formatting,
`executive._frame_diff`/`_changed` thresholds, `uitars._summarize_action`/`_target_from_thought`.
These run in CI with no rig.

**Stale-comment sweep** — remove the `evocua/evocua_agent.py is shadowed` notes
(`operate.py:39/44`, `run_probe.py`, `demo_parser_fix.py:7`); the file is gone (§1d).

---

## 6. Suggested migration sequence (self-serve, nothing stacked)

Each step is independently testable — run the mock server (`python agent_server.py --mock`)
and one real known-good goal ("Open Notepad and type: hello") after each, exactly your
"don't stack unknowns" discipline.

1. **`git init` + `.gitignore` + baseline commit.** Verify `git status` does **not** list
   `models/`. (No behavior change.)
2. **Add `config.py`; point ONE file at it** (start with `r4_client.py`). Confirm a real goal
   still drives the Pico. Then migrate the other IP/model/endpoint literals file by file,
   testing as you go.
3. **Create the `kvm_agent/` package skeleton** with `__init__.py`s. Move modules **with
   root-level shim files** left behind (`executive.py` → `from kvm_agent.orchestration.executive
   import *`) so `agent_server` and the `tools/` imports keep working unchanged. Test.
4. **Vendor-trim `evocua/`** (§2.4): copy 3 files, rewrite 3 imports, delete the clone +
   nested `.git`, delete the now-dead `sys.path` lines one entry point at a time. Test the
   EvoCUA path (`tools/operate.py --once` on the known calculator goal) — it's the only thing
   that touches the vendored code.
5. **Repoint the entry points** in `cli/` to import the package directly; delete the shims.
   Run the full mock + one live goal.
6. **Move docs/data**, add `pyproject.toml`, then layer in §3/§4 items **one at a time** with a
   measure run (`measure.py --k 10`) before/after each so you can attribute any rate change —
   same rigor your `FINDINGS_*` files already use.

Stop after step 4 if you only want the containment win; 5–6 are polish. The robustness items
(R1/R2) are independent of the move and can slot in whenever.

---

### Appendix — what I deliberately did **not** propose

- **No model changes** (per the ask): the set stays uitars-q4 / qwen2.5vl:7b / Qwen3-VL-8B; P4
  is a *serving* config, not a model swap.
- **No big-bang rewrite**: the modules are good; this is relocation + config extraction, not a
  redesign of the planner/executive/verifier architecture (which is working at 10/10).
- **No async rework of the rig lock**: one-task-at-a-time is correct for a single physical rig;
  leave `_RIG_LOCK` (`agent_server.py:39`) as is.
