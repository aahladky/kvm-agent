# Packaging — Status + Cutover Steps

_2026-06-21. Companion to `PLAN_2026-06-21_consolidation_and_optimization.md`._

## 1. What I built (all ADDITIVE — your running rig is untouched)

A complete `kvm_agent/` package now sits alongside your existing files. **Nothing at the
repo root was modified or deleted** — `agent_server.py`, `executive.py`, `tools/`, `evocua/`
are all exactly as they were, so the live system runs unchanged until *you* choose to cut over.

```
kvm_agent/
  __init__.py            seeds OPENAI_BASE_URL/KEY from config (preserves the implicit-env contract)
  config.py              ALL ips/ports/endpoints/model-names; defaults == today's literals
  hardware/pico_client.py   ← r4_client.py   (class R4 kept; PicoClient alias added)
  hardware/env.py           ← pico_env.py    (Camera + PicoEnv + shim)
  models/uitars.py          ← uitars_agent.py
  models/evocua.py          ← evocua_agent.py (imports now point at the vendored osworld)
  models/factory.py         ← cua_agent.py
  orchestration/planner.py  ← planner.py
  orchestration/executive.py← executive.py   (Verifier kept inside it)
  server/app.py             ← agent_server.py (sys.path hack removed)
  llm/ollama.py             shared client helper (additive; wired in at the P2 perf step, not yet)
  _vendor/osworld/mm_agents/…  the 3 upstream files the host actually imports
.gitignore  pyproject.toml   (new)
```

Config defaults are byte-identical to the old hardcoded literals (`192.168.0.183`,
`192.168.0.155:11434`, `uitars-q4`, `qwen2.5vl:7b`, `Qwen/Qwen3-VL-8B-Instruct`,
`1920x1080`), so importing through the package is behavior-identical. Change the laptop IP
or a model name in ONE place now (or via the matching env var).

## 2. Verification status — read this honestly

- **Structure + import wiring: verified** on the authoritative filesystem. Every module is
  complete (no truncation) and every `from kvm_agent...import` points at a real
  module/symbol. I confirmed this by direct file reads of every import line and file tail.
- **Execution test: blocked in my sandbox, NOT a code problem.** The Linux mount I run bash
  in serves stale/partial cached copies of just-written files (the lag your `CLAUDE.md`
  warns about — "verify via Read, not bash"). It got bad enough that bash reported the
  *provably-clean* `factory.py` as containing null bytes. So I could not get a clean
  `python -c "import …"` pass from here. The 9 modules whose cache happened to be fresh
  *did* import cleanly against stubbed deps; the 5 it choked on are exactly the 5 I rewrote,
  and the file tool shows all 5 correct on disk.
- **Therefore your first cutover step is a 5-second import check on Windows** (no mount, real
  deps) — that IS the execution test I couldn't run. Then the rig test.

## 3. Your cutover sequence (ordered; nothing stacks)

**Step 0 — import sanity (Windows, ~5s, do this first):**
```
cd C:\Dev\vllm
python -c "import kvm_agent.server.app, kvm_agent.orchestration.executive, kvm_agent.models.factory; print('imports OK')"
```
This exercises the whole graph with your real deps. If it prints `imports OK`, the package
is sound. (If anything errors, paste it to me — it'll be a one-line import fix.)

**Step 1 — git safety net (Windows; must be BEFORE the shim cutover so the baseline captures
your pristine root):**
```
cd C:\Dev\vllm
git init
git add -A
git status            # CONFIRM models/ , runs/ , scratch/ , evocua/ are NOT listed
git commit -m "baseline: working rig + additive kvm_agent package (pre-cutover)"
git switch -c refactor/packaging
```
The `.gitignore` I added already excludes `models/` (35 GB), `runs/`, `scratch/`, `evocua/`,
`__pycache__`. Double-check the `git status` line before committing.

**Step 2 — shim cutover (point the root modules at the package).** Replace each of these 7
root files with the 3-line shim in §4 (commit first so it's reversible). `agent_server.py`
and `tools/` need NO edits — they import the root names, which now resolve through the shims
to the package. After applying:
```
python -c "from executive import Executive, Verifier; from planner import run_goal; from pico_env import PicoEnv; print('shims OK')"
git add -A && git commit -m "cutover: root modules -> kvm_agent shims"
```

**Step 3 — rig test (you; I can't reach the hardware):**
1. `python agent_server.py --mock`  → hit it from Open WebUI; confirm the stream works.
2. Real run: start `agent_server.py`, then one known-good goal in Open WebUI:
   *"Open Notepad and type: hello from the package"* → expect `verify: true`, done.
3. Reliability: `python measure.py --k 10` → expect the same ~10/10 you had pre-refactor.

If all three pass, the move is proven. **Tell me and I'll proceed to the optimizations**
(R1 ACK, R2 wait-for-stable, P2 shared client) one at a time, each with a `measure --k 10`
before/after.

## 4. The 7 shims (paste each over the matching root file — Step 2)

`r4_client.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.hardware.pico_client."""
from kvm_agent.hardware.pico_client import *  # noqa
from kvm_agent.hardware.pico_client import R4, PicoClient, norm_key, NAME_ALIASES, R4_IP, R4_PORT  # noqa
```
`pico_env.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.hardware.env."""
from kvm_agent.hardware.env import *  # noqa
from kvm_agent.hardware.env import Camera, PicoEnv, PicoController, PicoPyAutoGUI  # noqa
```
`uitars_agent.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.models.uitars."""
from kvm_agent.models.uitars import *  # noqa
from kvm_agent.models.uitars import UITARSAgent  # noqa
```
`evocua_agent.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.models.evocua."""
from kvm_agent.models.evocua import *  # noqa
from kvm_agent.models.evocua import EvoCUAAgent  # noqa
```
`cua_agent.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.models.factory."""
from kvm_agent.models.factory import *  # noqa
from kvm_agent.models.factory import make_agent  # noqa
```
`executive.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.orchestration.executive."""
from kvm_agent.orchestration.executive import *  # noqa
from kvm_agent.orchestration.executive import Executive, Verifier  # noqa
```
`planner.py`
```python
"""Back-compat shim. Canonical code: kvm_agent.orchestration.planner."""
from kvm_agent.orchestration.planner import *  # noqa
from kvm_agent.orchestration.planner import (Planner, ClaudePlanner, LocalPlanner,
    HFPlanner, RulePlanner, run_goal, SYSTEM)  # noqa
```

> Want me to apply these for you? Once you've done Step 1 (the baseline commit), say so and
> I'll overwrite the 7 root files with these shims — it's reversible with `git checkout`.

## 5. Deferred — do NOT do these until the rig test passes

- **Delete `evocua/`** (the 55 MB upstream clone). The package vendors the 3 files it needs,
  but keep the clone on disk as a fallback until Step 3 proves the EvoCUA path works through
  `kvm_agent/_vendor`. Then delete it (`.gitignore` already hides it).
- **Remove the shims** and repoint `agent_server.py` + `tools/` imports straight at
  `kvm_agent.*`. Pure cleanup; do it after a few green runs.
- **Wire `llm/ollama.py`** into verifier/uitars/planner (the P2 shared-client win) — a
  separate, measured step.
- **Camera/Verifier file splits**: I kept those 1:1 inside `env.py` / `executive.py` for a
  lower-risk first move (the PLAN showed them split — trivial to do later).

## 6. Root → package map (reference)

| Root file (unchanged) | Canonical now lives at |
|---|---|
| `r4_client.py` | `kvm_agent/hardware/pico_client.py` |
| `pico_env.py` | `kvm_agent/hardware/env.py` |
| `uitars_agent.py` | `kvm_agent/models/uitars.py` |
| `evocua_agent.py` | `kvm_agent/models/evocua.py` |
| `cua_agent.py` | `kvm_agent/models/factory.py` |
| `planner.py` | `kvm_agent/orchestration/planner.py` |
| `executive.py` | `kvm_agent/orchestration/executive.py` |
| `agent_server.py` | `kvm_agent/server/app.py` (root runner stays; works via shims) |
| `boot.py`, `code.py` | unchanged (firmware; never imported by the host) |
