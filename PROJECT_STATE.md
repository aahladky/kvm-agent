# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-07-20 — post-sweep. Supersedes the 2026-06-21 snapshot (pre-Holo
stack; preserved in git history). Authoritative session detail: CLAUDE.md banners +
`docs/SESSION_2026-07-19_holo_focus_bug_and_native_prompt_port.md` +
`docs/REPORT_2026-07-19_problems.md`._

## 1. What it is

A computer-use agent where **nothing is installed on the target**. A local vision
model sees the target's screen over an HDMI capture card and drives it through a
physical USB-HID injector. The target sees only a monitor + USB mouse/keyboard —
OS-agnostic, undetectable. Pure curiosity project.

## 2. The live system (current iteration)

- **LOOP** — `agent_loop_holo.py`: one tool-call per step, observe→act with
  verify-and-retry. Model: **Holo3.1-35B** served locally via **llama-swap**
  (`http://127.0.0.1:9292/v1`, SYCL llama-server on the Arc Pro B70, modelctl-managed).
- **HID** — Pi 5 + Pico 2 W **appliance** (`appliance/`): Pico runs `pico_fw/`
  (C/TinyUSB, ported from PiKVM 2026-07-18, CRC16 binary protocol over 3-wire UART);
  Pi 5 runs `hid_bridge.py` (HTTP API, `http://192.168.0.29:8080`). Host client:
  `kvm_agent/hardware/appliance.py` via `env.py`. The WiFi-Pico path is retired.
- **CAPTURE** — HDMI capture card via cv2 (V4L2 on the Linux host).
- **TARGET** — Win11 VM `win11-agent` (libvirt): reverted to the `clean-desktop`
  snapshot + cold boot between WAA tasks (`kvm_agent/hardware/vm.py`).
- **EVAL** — WindowsAgentArena via `waa/runner.py`. Verifier = **holo3.1
  self-grade** (fresh prompt on the final frame; zero model swaps — the
  gemma4-dense grader caused 16 evictions in 45 min on 2026-07-18).
- **EVIDENCE** — every run records per-step frames + raw model output +
  `reasoning_content` to `runs/<tag>_<time>/` (`RunRecorder`). First tool on any
  failed run: `tools/show_reasoning.py`. A/B harness: `tools/shakedown_ab.py`.

## 3. Solved (verified)

- **Win32 focus-transfer bug** (2026-07-19): apps launched via Win+R don't reliably
  receive real keyboard focus; keystrokes went to the desktop. Fix in `_execute()`:
  a `type` with no visible screen change clicks to force focus before retrying.
  Verified by replay; notepad task passes at history 1 and 2.
- **WAA server terminal-window leak** (present in every WAA run since adoption):
  patched + re-baked into the `clean-desktop` snapshot.
- **Pico HID reliability**: CircuitPython firmware was structurally unsound;
  replaced wholesale by the PiKVM port (`appliance/pico/` retired).
- Blame ledger so far: **model 0, our code 3** (`AGENTS.md` §5).

## 4. Open problems

- **windows_calc went 0/9** across history depths 1-3: inconsistent WinUI3
  date-picker widget (live double-reproduced) + a stuck-popup click bug distinct
  from the focus bug (session doc §4). Native holo-desktop-cli passes the same
  task — same model, different pipeline.
- **Native prompt port** (loop-detection instruction, `note` param + persistent
  notes block, stricter termination checklist): real but partial effect; notes saw
  zero uptake; not yet validated on the easier task class it targets (§5, §7).
- **Store auto-update pause** on the target expires **~2026-08-23** — re-apply
  (steps in memory `waa_store_autoupdate_pause.md`).
- History-depth is not a dial that fixes the hard task class (3-depth shakedown:
  5/17, 7/16, 7/15).

## 5. Retired (2026-07-20 sweep — `_archive/old-stack/`)

EvoCUA/UI-TARS/B580-planner stack, Open-WebUI server path, orchestration
executive/planner, battery, hindsight memory, Ollama-based verifier
(`qwen2.5vl:7b` via laptop — Ollama no longer installed), WiFi R4 Pico transport
(`pico_client.py`), CircuitPython firmware (`boot.py`/`code.py`), `rig.py` +
`preflight.py` (checked the dead stack). Nothing live imports from `_archive/`.

## 6. House rules

`AGENTS.md` is law for every agent: all artifacts in `runs/`, nothing in hidden
dirs, the model is the last suspect, no ghost generations, sessions end
commit-or-revert with this file updated.
