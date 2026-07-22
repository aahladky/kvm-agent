# Project State — KVM-over-IP Computer-Use Agent

_Snapshot: 2026-07-20 — physical-target move. Supersedes the 2026-07-20 post-sweep
snapshot (git history). Design: `docs/PLAN_2026-07-20_physical_target_move.md`._

## 1. What it is

A computer-use agent where **nothing is installed on the target**. A local vision
model sees the target's screen over an HDMI capture card and drives it through a
physical USB-HID injector. The target sees only a monitor + USB mouse/keyboard —
OS-agnostic, undetectable. Pure curiosity project.

## 2. The live system (current iteration)

- **LOOP** — `agent_loop_holo.py`: batched tool calls per step (native semantics:
  calls in a batch see each other's effects; only the batch's final screenshot goes
  back), observe→act with the frame-seq freshness floor (paired via seq numbers). Model: **Holo3.1-35B**
  served locally via **llama-swap** (`http://127.0.0.1:9292/v1`, SYCL llama-server on
  the Arc Pro B70, modelctl-managed).
- **HID** — Pi 5 + Pico 2 W **appliance** (`appliance/`): Pico runs `pico_fw/`
  (C/TinyUSB, PiKVM port, CRC16 binary protocol over 3-wire UART); Pi 5 runs
  `hid_bridge.py` (HTTP API, `http://192.168.0.29:8080`). Host client:
  `kvm_agent/hardware/appliance.py`. `clear_hid` (all-keys-up) runs on connect + close.
- **CAPTURE** — HDMI capture card via cv2 (V4L2 on the Linux host), `Camera` +
  `FrameBuffer` (monotonic frame seq) in `kvm_agent/hardware/env.py`.
- **TARGET** — physical spare laptop (Ubuntu/GNOME as of 2026-07-21; formerly
  Windows 10), lid closed, HDMI out → capture
  card → passthrough to the user's monitor. **The laptop renders at 1280x720; the
  chain (GPU or capture card) upscales to the 1920x1080 the camera delivers** —
  verified live 2026-07-21: the desktop fills the frame, so pixel FRACTIONS are
  consistent end-to-end (projection basis == bridge scale == USB wire fraction)
  and clicks land correctly. Costs are image-quality only (model input and
  evidence frames are upscaled 720p content). Set `SCREEN_W/H=1280x720` in
  `.env.local` for native capture, or set the laptop to 1080p — the measure-then-
  `set_screen` chain keeps the bases locked either way. Power/reset seam:
  `kvm_agent/hardware/target.py` (v1 MANUAL reboot; WoL/smart-plug backend deferred —
  decide with hardware in front of us). Reset strategy: reboot between tasks; disk
  image (Clonezilla) as the determinism backstop. `verify_hid`'s round-trips are
  shell-anchored via `CFG.target_shell` ("gnome" default since 2026-07-21: Super
  tap → Activities, Esc closes; Activities corner click, top-left — "windows"
  keeps win+r/Start for a Windows target); verified live on GNOME 2026-07-21
  (kbd diff 131.0, mouse diff 134.9).
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
- Review batch-1 fixes (2026-07-21, from the full-scope repo review): bridge
  screen-size sync at env bring-up (`set_screen` — existed on both ends, called by
  neither; closes the silent click-stretch hole), model-call exceptions contained as
  dropped steps (`run()` always finishes the recorder; one API error no longer kills
  a battery), planning-only steps exempt from the frozen-screen abort, `drag_to`
  re-asserts the tracked start before button-down, `jinja2` declared / `requests`
  dropped in `pyproject.toml`. Coverage: `tests/test_agent_loop.py` (offline, 12 checks).
- Review batch-2 fixes (2026-07-21): `wait_until_stable` returns a status
  ("stable"/"timeout"/"dead"); capture stalls/dead-capture windows are surfaced
  into the step's `<tool_output>` and the recorded step's `warnings` instead of a
  swallowed print; `boot()` runs the camera-verified HID gate by default
  (`verify=False` to bypass — the battery keeps its interactive per-task gate), so
  REPL sessions no longer click into a half-dead HID silently. (The "dead" status
  initially covered only the never-delivered case; the wedged-capture case needed
  the seq-aware fix in the second-review round below.)
- Review batch-3 hygiene (2026-07-21): CLAUDE.md pruned to a corrected header +
  trust-ordered pointers (the ~80 KB retired-stack body survives in git history);
  test suite is pytest-collectable while staying script-runnable, with a
  declared `[test]` extra and new holo message-layer coverage
  (`tests/test_holo_messages.py`); the tile-max metric and its threshold have a
  single home (`kvm_agent.hardware.env` + `CFG.frame_change_threshold`);
  `verify_hid` no longer imports the root app script; dead code dropped
  (`_frame_png_full`, `drop_bottom_row`, ASCII-only `stage1_ping_test.py`).
- Review batch-4 fixes (2026-07-21): `Camera` bring-up failure raises catchable
  `RuntimeError` instead of `SystemExit`; `_scalar` shape-guards coordinates (a
  nonsense list raises instead of inventing a midpoint click); `_req` surfaces the
  bridge's HTTP error detail (502/404 bodies) instead of a bare "transport error";
  `show_reasoning.py` speaks the live action vocabulary (hotkey/double_click/
  hold_and_tap, `keys` field) and the batched step-record shape; battery summary is
  foldered (`runs/battery_<ts>/results.json`) and the resolution A/B probe writes
  its results to `runs/`.
- Second-review fixes (2026-07-21, all 12 WRONG items; suite now 53 tests).
  Evidence: `summary.json` action lists read the batched step shape; the evidence
  PNG and model-input JPEG derive from ONE buffer read and the system prompt
  travels in each run's `meta.json`; battery scoring is fail-closed over ALL tasks
  (`total_tasks`/`graded`/`complete`). Feedback/robustness: exec-error steps count
  against `STUCK_LIMIT` (the abort was dead code) and their error `<tool_output>`
  reaches the model; `wait_until_stable` is seq-aware (a wedged capture reports
  "dead", not "stable"); `combo()` fails closed on any unknown key; unsupported /
  no-op actions report `NOT executed`; `type()`'s HTTP timeout scales with text
  length; screen size is measured from the actual frame after bring-up (projection
  AND the `set_screen` push); the freshness floor starts AFTER the HID fire;
  `CURSOR`/`PLAN` reset per run; `repeat_count` clamped; `HOLO_HISTORY_IMAGES=0`
  refused; `finish_reason='length'` logged. Firmware: `ph_usb_send_clear` no
  longer injects a phantom wheel scroll (upstream PiKVM bug) — **needs a Pico
  reflash**, and the `pikvm_proto.py` combo change **needs deploying to the Pi 5**.

## 4. Open problems

- **First honest baseline: INCOMPLETE.** Battery attempt 2026-07-21 scored 1/2
  (notepad PASS, calc 0/20 — the three-way Blame-Ledger row in `AGENTS.md` §5)
  and was abandoned at task 2's grade prompt. Remaining: 3 tasks + a clean rerun.
  Session doc §Battery attempt has the full incident list and follow-ups.
- **Tool-result signal is semantically misleading**: changed/unchanged binary
  confirmed real-but-irrelevant pixels (taskbar focus visuals) as action success
  at decision-critical steps (2026-07-21). Needs magnitude/region — fold into
  the structured-output session.
- **Goldfish-memory amnesia is load-bearing**: model's own reasoning flagged it
  ("screenshots are being evicted"). `HOLO_HISTORY_IMAGES=2` battery A/B queued.
- **~70s OS dead window post-reboot** (delivered HID swallowed, steps 0-9 of the
  calc run): cause unknown; psr.exe zip from that run is the outstanding evidence.
- **Post-reboot half-dead HID recurs** (I2 class, physical): gate exists
  (`target.verify_hid` + replug loop in battery); automate with the power backend.
- windows_calc class (WinUI3 date-picker + stuck-popup, 2026-07-19): re-observe
  on Win10's classic calc.
- Store auto-update pause expiry (VM-era note; re-assess for the laptop).
- Deferred: power-control backend, firmware HID watchdog, automated fail-closed
  vision grading (schema slot exists), superseded adoption (structured-output
  rearchitecture + resolution sync).

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
