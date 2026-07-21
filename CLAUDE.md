# Hardware Computer-Use Agent — Session Handoff

★★★ ALL AGENTS: read and follow AGENTS.md (Agent Working Agreement) in this repo
before touching anything. Output goes in runs/ and nowhere else; nothing
project-related in hidden dirs; the model is the last suspect. ★★★

## What this project is

A KVM-over-IP-style computer-use agent where nothing is installed on the target
machine. A vision model sees the target's screen via HDMI capture, decides actions,
and a physical USB-HID device injects mouse/keyboard. Target sees only a monitor +
a USB mouse/keyboard — undetectable, OS-agnostic. Pure curiosity project, no
practical application.

## Where the truth lives

- **PROJECT_STATE.md** — the CURRENT system, solved problems, open problems,
  retired stacks. Read it before assuming anything below is complete.
- **AGENTS.md** — the working agreement (output discipline, blame ledger). Law.
- **docs/** — dated SESSION_* / FINDINGS_* / REVIEW_* files: the full history,
  including docs/REVIEW_2026-07-21_repo_review.md (whole-repo review + errata).
- **git log** and **_archive/old-stack/** — everything retired. `_archive/` is
  reference only; nothing live may import from it.

(2026-07-21: this file was ~82 KB of layered session history that contradicted its
own header — the live harness had moved twice since most of it was written. The
history was not lost: it is in git and in docs/. Do not grow this file back;
session close-out notes go in PROJECT_STATE.md and docs/SESSION_*.)

## The live stack (snapshot 2026-07-21 — details in PROJECT_STATE.md)

- **agent_loop_holo.py** — the agent loop (Holo3.1-35B via local llama-swap,
  native-verbatim line: steps are BATCHES of tool calls, results return through
  <tool_output> with a tile-diff magnitude+region signal).
- **kvm_agent/** — the package: config.py (CFG — every IP/port/model/threshold),
  hardware/ (appliance HTTP client, camera/env, target seam + HID gate), models/
  (holo adapter + parser fixtures), instrumentation/ (RunRecorder), llm/.
- **appliance/** — Pi 5 HTTP→UART bridge (pi5/) + Pico 2 W C/TinyUSB firmware
  (pico_fw/, PiKVM port). The old CircuitPython firmware is retired at
  _archive/firmware_old/appliance_pico/.
- **tools/** — battery.py (human-graded task battery), hid_smoke.py,
  probe_resolution_ab.py, show_reasoning.py (first stop on any failed run).
- **tests/** — offline suite, pytest-collectable AND directly runnable
  (`pytest tests/` or `python tests/test_x.py`). Run it before rig time.
- **Target**: physical Windows 10 laptop, HDMI→capture card, manual reboot seam.

## Repo layout facts

- Tracked content is small (~2 MB); models/runs/scratch are NOT in the repo.
- `runs/` and `scratch/` are host-side symlinks on the rig (runs →
  ~/data/kvm-agent/runs, scratch → ~/tmp/kvm-agent-scratch, auto-deleted after
  14 days); a fresh clone does not have them — anything worth keeping goes to
  runs/ or the repo, never scratch/.
- New library code → kvm_agent/<area>/. Entry points → repo root. Diagnostics →
  tools/. Tests → tests/. Notes → docs/. Every IP/port/model/threshold → CFG in
  kvm_agent/config.py — never hardcoded in a module.
- Known packaging gap: agent_loop_holo.py is a root script (not packaged); tests
  and tools import via path insertion (tests/conftest.py).

## Working style

Aaron is a highly technical tinkerer (electronics, soldering, Linux, homelab).
Direct, technically-honest answers; incremental isolated testing ("don't stack
unknowns"); empirical decisions over premature optimization. Lead with concrete
steps, isolate variables, watch one signal at a time. Every action must assert its
post-condition against the SCREEN — no success signal decoupled from reality, no
primitive that silently no-ops.
