# PLAN — Move the Holo stack from the win11-agent VM to the physical Win10 laptop (2026-07-20)

Status: APPROVED design, pre-implementation. Supersedes the VM-based topology described
in `PROJECT_STATE.md` §2 (TARGET/EVAL bullets) once landed.

## 1. Goal & scope

Move the live Holo stack (capture + appliance + `agent_loop_holo.py`) off the
`win11-agent` libvirt VM and onto the physical Windows 10 spare laptop, and produce the
first honest, human-graded capability baseline. Motivation: the VM introduced failures
that were hard to attribute (Win32 focus transfer, SPICE/virt-viewer collapse, snapshot
contamination, USB-passthrough dead-mouse). Bare metal removes that whole suspect class.

Explicitly OUT of scope: automated grading, power-control automation, firmware changes,
model/prompt changes. `agent_loop_holo.py` loop logic is unchanged except where the
trust fixes in §4 touch it.

## 2. Target topology

- Win10 spare laptop, lid closed. HDMI out → capture card (V4L2 on the Linux host) →
  passthrough to the user's monitor for human oversight. Single display path, no
  mirroring.
- Capture, appliance (Pi 5 + Pico 2 W, UART, CRC16 binary protocol), and model serving
  (holo3.1 via llama-swap at 127.0.0.1:9292) are unchanged.
- `kvm_agent/hardware/target.py` — new seam replacing `vm.py`:
  - `reboot()` — v1 is MANUAL: prints "power-cycle the laptop, press Enter when the
    desktop is up". `wol` / `smartplug` backends slot in later behind the same
    signature; the power-control decision is deliberately deferred until the hardware
    is in front of us (WoL support / power-on-AC / smart-plug availability unknown).
  - `is_up()` — v1: capture card delivers frames + frame-diff against a boot-completed
    reference, or simply folded into the manual prompt; decided at implementation.
- Reset strategy: reboot-only between tasks; dirty state tolerated by task design.
  Backstop: full disk image of the clean Win10 state (Clonezilla USB), restored when
  determinism matters.

## 3. Retirements (single commit, per AGENTS.md §3 — no ghost generations)

- `waa/` (runner + WAA coupling), `kvm_agent/hardware/vm.py`,
  `tools/shakedown_ab.py` → `_archive/`
- EvoCUA exec-shim in `env.py`: `PicoPyAutoGUI`, `PicoController.execute_python_command`,
  `PicoEnv.step/reset/evaluate` → `_archive/`. KEEP the live surface: `Camera`,
  `PicoEnv.observe()/_settle/close()`, `make_hid_client()`, `wait_until_stable`
  (live — called directly by `agent_loop_holo.py:216,246`; fixed per §4.1, not archived). (Rationale: `agent_loop_holo.py` bypasses the shim entirely;
  the shim is the compat layer for the archived EvoCUA generation. Code review
  2026-07-20 confirmed real defects in it — swallowed exec errors, `position()`→(0,0),
  unpaced double-click, no-op keyDown/keyUp — all with zero live consumers. Archive,
  don't patch.)
- Carried over from the 2026-07-20 deep dive (same commit):
  - `appliance/pico/`, `appliance/pi5/send.py`, `appliance/host/stage2_verify.py` →
    `_archive/` (retired CircuitPython generation; AGENTS.md §3 violation)
  - `tools/wol.py` → `_archive/` (dormant; polls the retired WiFi Pico. Resurrect as
    the `wol` backend of `target.py` if the power decision goes that way)
  - `kvm_agent/llm/ollama.py::ollama_generate()` — delete (zero callers, Ollama gone);
    keep `openai_client` (live, used by `models/holo.py`)
  - Dead CFG fields (zero live consumers, grep-verified): `pico_ip/pico_port`,
    `hid_kind`, `ollama_base/openai_base/openai_key`, `executor_model`,
    `verifier_model`, `verifier_local_model`, `verifier_max_tokens`, all `planner_*`,
    `closed_loop*`, `tesseract_cmd`, `hindsight_*`, `anthropic_key`
  - `HOLO_INTEGRATION_PLAN.md` — stamp SUPERSEDED at the top (phases I0–I5 done;
    deliverables describe the retired WiFi-Pico stack)
  - `pyproject.toml`: drop deps only imported in `_archive/` (`anthropic`, `backoff`,
    `fastapi`, `uvicorn`, `pytesseract`, `huggingface_hub`); add `requests`; remove the
    commented console-script pointing at archived `server.app:main`
  - Stale-comment sweep in files we touch anyway (env.py docstring, holo.py:133,
    agent_loop_holo.py:8, run_log.py:3 ghost citation)
  - Root/tools/tests `__pycache__` cleanup (ghost bytecode of archived modules)

## 4. Harness trust fixes (from the 2026-07-20 review triage — live blast radius)

1. **Settle metric** — `wait_until_stable` currently uses the 160×90 whole-frame mean
   (the metric flaw #4 discredited). Port it to the tile-max metric used by
   `_frame_diff_score`; re-validate the threshold against saved laptop frames from the
   first shakedown run (analog capture noise floor differs per source).
2. **Frame freshness** — `Camera` gains a monotonic frame counter + timestamp;
   `read()` returns (copy, seq). Post-action observation/verify waits for a frame with
   seq newer than the action's fire time: exact before/after pairing instead of
   approximate. (Resolves harness-review finding #6, open since 2026-07-18.) Also
   closes the unlocked `png_bytes` read.
3. **`clear_hid` end-to-end** — proto already has it (`pikvm_proto.py:177`). Add the
   `hid_bridge.py` route, `ApplianceClient.clear_hid()`, and call it on connect and in
   `PicoEnv.close()`. Kills the latched-modifier-after-fault class. (Firmware watchdog
   deferred: `combo()` self-releases; the latch window is link-death-mid-combo only.
   Noted here as known-narrow.)

## 5. Battery runner — human-graded

- New `tools/battery.py`: task list as JSON (`instruction`, step budget, setup notes).
  Per task: `target.reboot()` → wait ready → run the Holo loop → record everything via
  `RunRecorder` into `runs/battery_<ts>/<task>/`: per-step 720p model-input frames,
  full-res evidence frames, raw model output, `reasoning_content`, action log,
  timings, final frame.
- Grading contract for this stage: **the user is the grader.** At task end the tool
  shows the final frame (step montage on request) and prompts `pass/fail + one-line
  note`, written to `results.json`. Schema carries `grader: "human"` so a fail-closed
  vision grader can be slotted in later without changing the runner. No automated
  grade in v1 — and per finding #8, no silent `None`-is-pass path may ever exist.
- Independent ground-truth channel: Windows Steps Recorder (psr.exe, built-in) runs on
  the laptop during battery runs. It logs what Windows actually received (OS-side
  screenshots + click annotations) vs what the capture card saw — diverging channels =
  capture/HID pipeline; agreeing channels = model/harness. Practical: raise psr's
  100-capture default cap; MHT zips retrieved manually into the run folder; manifest
  records whether psr was active.

## 6. First physical session (shakedown, in order — live gates per AGENTS.md §4)

1. Cable the laptop (lid closed, HDMI→card→passthrough), boot, confirm the capture
   sees Windows; record the native resolution.
2. HID smoke test: appliance `probe()` (both collections) → type a known string into
   Notepad via HID → OCR-verify on the full-res frame. One test exercises every
   actuation/observation layer; also the prototype deterministic check for later.
3. One task end-to-end, full recording, human-graded.
4. Calibration battery (5–8 tasks: notepad/calc/paint/settings/clock class), reboot
   between tasks via manual `target.reboot()`.
5. Clonezilla disk image of the clean Win10 state — the reset backstop.

## 7. Testing

- Offline (cheap gates before rig time): tile-max settle unit test against existing
  frame fixtures; frame-counter pairing with a fake camera; `clear_hid` client against
  a mocked bridge; `target.py` manual backend (mocked stdin); `tests/test_frame_diff.py`
  must keep passing.
- Live: the shakedown sequence in §6 is the acceptance test.

## 8. Close-out

- `PROJECT_STATE.md` rewritten: new topology, VM/WAA stack retired, human-graded
  battery as the eval contract, open problems carried forward (windows_calc class,
  Store auto-update pause expiry ~2026-08-23).
- `docs/SESSION_*` entry for the implementation session; Blame Ledger untouched
  (no new rows).
- Session ends commit-or-revert, `git status` clean.

## 9. Deferred / open

- Power-control backend decision (WoL vs smart plug vs hybrid) — `target.py` is the
  seam; decide with hardware in front of us.
- Firmware-side HID watchdog (see §4.3).
- Automated fail-closed vision grading — slot exists in `results.json` schema.
- WindowsAgentArena comparability — abandoned deliberately (in-guest getters violate
  nothing-on-the-target; vision/human grading keeps the attribution chain short).
