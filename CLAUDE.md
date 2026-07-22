Hardware Computer-Use Agent — Session Handoff

★★★ ALL AGENTS: read and follow AGENTS.md (Agent Working Agreement) in this repo
before touching anything. Output goes in runs/ and nowhere else; nothing
project-related in hidden dirs; the model is the last suspect. ★★★

What this project is

A KVM-over-IP-style computer-use agent where nothing is installed on the target machine. A vision model sees the target's screen via HDMI capture, decides actions, and a physical USB-HID device injects mouse/keyboard. Target sees only a monitor + a USB mouse/keyboard — undetectable, OS-agnostic. Pure curiosity project, no practical application.

Repo layout (cleaned 2026-07-20, corrected 2026-07-21)

Code (~2 MB, tracked):
- kvm_agent/          — canonical package (config, hardware, instrumentation, llm, models). Active.
- agent_loop_holo.py  — CURRENT agent loop (Holo3.1, physical target — Ubuntu/GNOME
  as of 2026-07-21; see PROJECT_STATE.md). Where new work happens.
- appliance/, tools/, tests/, docs/ — current-gen support: appliance code,
  harnesses (battery runner), unit tests, session reports.
- appliance/pico_fw/  — CURRENT Pico firmware (C/TinyUSB, ported from PiKVM 2026-07-18).
  The old CircuitPython firmware is RETIRED — it lives at
  _archive/firmware_old/appliance_pico/, never deploy.
- _archive/old-stack/ — the retired generations: EvoCUA/UI-TARS/Open-WebUI stack, and
  (2026-07-20 physical move) the libvirt VM stack, WindowsAgentArena (waa/), the
  pyautogui exec-shim, wol.py, and the un-ported appliance scripts (send.py,
  stage2_verify.py). Reference only; nothing in the live tree may import from it.

Data (untracked, gitignored, moved out of the repo 2026-07-20):
- runs    -> ~/data/kvm-agent/runs      (symlink; benchmark evidence)
- scratch -> ~/tmp/kvm-agent-scratch    (symlink; auto-deleted after 14 days —
  nothing you want to keep goes here; use runs/ or _archive/ instead)

═══════════════════════════════════════════════════════════════
Where the truth lives
═══════════════════════════════════════════════════════════════

This file was pruned 2026-07-21: the ~80 KB of layered session notes below this
header described retired stacks (EvoCUA/UI-TARS/B580 planner, the libvirt VM
target, WAA/Firefox/calc benchmarks) and contradicted the header above. The full
text is in git history (parent of the prune commit).

- CURRENT STATE: PROJECT_STATE.md — architecture, solved problems, open problems.
- THE LAW: AGENTS.md — output discipline, blame ledger, session shape.
- SESSION REPORTS: docs/SESSION_*, docs/REPORT_*, docs/FINDINGS_* — dated evidence.
- NATIVE HOLO FORMAT: docs/FORMAT_NOTES_holo.md + docs/native/ (the runtime prompt
  template local-desktop-2026-06-12.j2 is a LOAD-BEARING runtime asset loaded by
  kvm_agent/models/holo.py, not documentation).

When these disagree, trust in this order: AGENTS.md > PROJECT_STATE.md > dated
docs (newest first) > this file.
