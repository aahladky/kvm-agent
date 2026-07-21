# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-07-20 — physical-target move. Supersedes the 2026-07-20 post-sweep
snapshot (git history). Design: `docs/PLAN_2026-07-20_physical_target_move.md`._

## 1. What it is

A computer-use agent where **nothing is installed on the target**. A local vision
model sees the target's screen over an HDMI capture card and drives it through a
physical USB-HID injector. The target sees only a monitor + USB mouse/keyboard —
OS-agnostic, undetectable. Pure curiosity project.

## 2. The live system (current iteration)

- **LOOP** — `agent_loop_holo.py`: one tool-call per step, observe→act with
  verify-and-retry (paired to the action via frame seq numbers). Model: **Holo3.1-35B**
  served locally via **llama-swap** (`http://127.0.0.1:9292/v1`, SYCL llama-server on
  the Arc Pro B70, modelctl-managed).
- **HID** — Pi 5 + Pico 2 W **appliance** (`appliance/`): Pico runs `pico_fw/`
  (C/TinyUSB, PiKVM port, CRC16 binary protocol over 3-wire UART); Pi 5 runs
  `hid_bridge.py` (HTTP API, `http://192.168.0.29:8080`). Host client:
  `kvm_agent/hardware/appliance.py`. `clear_hid` (all-keys-up) runs on connect + close.
- **CAPTURE** — HDMI capture card via cv2 (V4L2 on the Linux host), `Camera` +
  `FrameBuffer` (monotonic frame seq) in `kvm_agent/hardware/env.py`.
- **TARGET** — physical **Windows 10 spare laptop**, lid closed, HDMI out → capture
  card → passthrough to the user's monitor. Power/reset seam:
  `kvm_agent/hardware/target.py` (v1 MANUAL reboot; WoL/smart-plug backend deferred —
  decide with hardware in front of us). Reset strategy: reboot between tasks; disk
  image (Clonezilla) as the determinism backstop.
- **EVAL** — human-graded battery: `tools/battery.py` + task JSON. The user grades
  pass/fail per task from the recorded evidence; no automated grade exists and no
  uncertain grade can masquerade as a pass (finding #8). Steps Recorder (psr.exe) on
  the laptop is the independent ground-truth channel (what Windows actually received
  vs what the capture card saw).
- **EVIDENCE** — every run records per-step frames + raw model output +
  `reasoning_content` to `runs/<tag>_<time>/` (`RunRecorder`). First tool on any
  failed run: `tools/show_reasoning.py`.

## 3. Solved (verified)

- Win32 focus-transfer bug (2026-07-19, click-to-focus retry in `_execute()`).
- WAA server terminal-window leak (patched + re-baked; moot post-WAA).
- Pico HID reliability (PiKVM firmware port; WiFi-Pico path retired).
- Harness trust (2026-07-20): tile-max settle metric, frame-seq before/after pairing
  (finding #6 closed), `clear_hid` wiring.
- Blame ledger: **model 0, our code 3** (`AGENTS.md` §5).

## 4. Open problems

- **First honest baseline**: the physical shakedown battery (5 tasks,
  `tools/battery_tasks_shakedown.json`) has not yet run — all prior numbers came from
  the VM stack and don't transfer.
- windows_calc class: WinUI3 date-picker inconsistency + stuck-popup click bug
  (2026-07-19 session doc §4). Win10's classic calc may not reproduce it — re-observe.
- Settle threshold (3.0) is calibrated on the VM-era capture chain; re-validate on
  the laptop panel's noise floor on the first physical run.
- Store auto-update pause expiry (VM-era note; re-assess for the laptop).
- Deferred: power-control backend, firmware HID watchdog, automated fail-closed
  vision grading (schema slot exists: `grader` field in battery results).

## 5. Retired

2026-07-20 sweep: EvoCUA/UI-TARS/B580-planner stack, orchestration, battery-v1,
hindsight, Ollama verifier, WiFi Pico, CircuitPython firmware, rig/preflight.
2026-07-20 physical move: **libvirt VM stack (`vm.py`, win11-agent),
WindowsAgentArena (`waa/`), the EvoCUA pyautogui exec-shim, `wol.py`,
`shakedown_ab.py`, `appliance/pico/` + `send.py` + `stage2_verify.py`** — all in
`_archive/`. Nothing live imports from `_archive/`.

## 6. House rules

`AGENTS.md` is law for every agent: all artifacts in `runs/`, nothing in hidden
dirs, the model is the last suspect, no ghost generations, sessions end
commit-or-revert with this file updated.
