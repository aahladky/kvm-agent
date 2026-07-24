# Session 2026-07-20 — Physical Target Move

Plan: `docs/PLAN_2026-07-20_physical_target_move.md`. State: `PROJECT_STATE.md`.

## What changed

The VM/WAA stack was retired and the agent moved to a physical Windows 10 laptop
target, executed as the task list in `docs/PLAN_2026-07-20_physical_target_move.md`:

1. Archive sweep — libvirt VM stack (`vm.py`, win11-agent), WindowsAgentArena
   (`waa/`), the EvoCUA pyautogui exec-shim, `wol.py`, `shakedown_ab.py`,
   `appliance/pico/` + `send.py` + `stage2_verify.py` → `_archive/`.
2. Excise the EvoCUA exec-shim from `env.py`.
3. `clear_hid` (all-keys-up) wired end-to-end on connect + close.
4. `FrameBuffer` freshness (monotonic frame seq) in `kvm_agent/hardware/env.py`.
5. `_execute` verify frames paired to the action (`wait_newer` floor, both the
   first fire and the retry path).
6. Tile-max settle metric in `wait_until_stable` (flaw #4 metric fully retired).
7. `kvm_agent/hardware/target.py` — manual power/reset seam (v1).
8. `tools/battery.py` — human-graded task battery runner.
9. Config/packaging cleanup — retired-stack fields dropped, deps slimmed.
10. Docs close-out (this file, `PROJECT_STATE.md`, header/comment truth pass).

Execution was subagent-per-task with per-task review and a final whole-branch
review ("with fixes": bridge-redeploy step, battery crash-hardening, docstring
truth — all fixed in `85d3631`). Branch: `feature/physical-target-move`.

## Live shakedown results

Rig session 2026-07-21 (early AM). **Superseded-branch discovery**: the Pi ran the
`superseded` branch's newer bridge (wire logging, `/hid/set_screen`), which the
packaging line lacked. Resolution per user: superseded's rearchitecture +
resolution-sync adoption is deferred to a future session; for now the deployed
bridge was adopted as-is PLUS the new `/hid/clear` route (commit `98b29bc`), so
nothing deployed was regressed. Redeployed + restarted `hid-bridge.service`;
`/health`, `/hid/probe`, `/hid/clear` all verified live.

- **Capture gate**: clean 1920×1080 Win10 desktop (lid closed, HDMI→card→
  passthrough). Native res matches the whole pipeline; no env overrides needed.
- **HID smoke** (`runs/shakedown_20260721_000523/`): probe `kbd=1 mouse=1`;
  Notepad opened via HID (Win+R → notepad → Enter); typed string OCR-verified
  on the full-res frame. Every actuation/observation layer works on the laptop.
- **First task end-to-end** (`runs/shakedown_notepad_type_20260721_001008/`):
  `notepad_type` via `agent_loop_holo.run()`, 4 steps, 94s (75s = first-call
  model load). PASS, verified on the final frame by eye (not the self-report):
  Notepad open with the full sentence typed. Actions: click search box → click
  Notepad tile → type → finished.
- **Full battery + Clonezilla image**: Clonezilla pending; battery ABORTED after
  2/5 tasks (see below).

### Battery attempt (2026-07-21 07:41, abandoned at task 2's grade prompt)

Score 1/2: `notepad_type` PASS (human-graded), `calc_multiply` fail by exhaustion
(0/20 steps). Three incidents, each root-caused and either fixed or recorded:

1. **Post-reboot half-dead HID (I2 class, physical edition).** The battery's
   power-cycle brought the composite device up keyboard-alive/mouse-dead with the
   probe flags LYING (`mouse_online=true`, wire log ACKing everything, camera
   showing non-delivery). Recovery: replug the Pico's USB. Fixes landed:
   camera-verified post-reboot HID gate in `target.verify_hid` (Win+R + Start-click
   round-trips, contamination-hardened with pre-Esc + retry after a leftover Run
   dialog read as "keyboard dead"); wired into `tools/battery.py` after every
   `target.reboot()`. (`7725d3b`, `3f99c62`)
2. **Model-invented key names.** `winkey` 502'd and killed the first battery run
   at step 1; `winleft` was correctly dropped mid-run after the fix. Fixes: alias
   sets added (`winkey/windows/super/meta`, `winleft/winright/leftwin/rightwin`),
   and `run()` now treats a bridge-rejected action as a dropped action in the
   stuck counter instead of crashing the battery. (`7725d3b`, `8afddba`)
3. **calc_multiply 0/20 — the Blame-Ledger row** (`AGENTS.md` §5, 2026-07-21).
   Prematurely called "environment exonerated, model limitations"; the §2 walk
   (frames + raw reasoning + tool results) showed a three-way split: ~70s OS dead
   window (delivered input swallowed, steps 0-9, cause unknown — psr zip is the
   outstanding target-side evidence); "screen changed" tool result technically
   true but semantically false at steps 4,5,11 (taskbar focus visuals, tile
   grid(8,1) — the model typed blind, citing the false confirmations in its
   reasoning); goldfish memory (model: "screenshots are being evicted") with the
   click-repeat guard disabled by design. Genuine model faults: `winleft`,
   double-× (M3).

**Open follow-ups for the next session** (in rough priority):
- Tool-result signal must report WHAT changed (magnitude/region), not a bare
  changed/unchanged binary — fold into the deferred structured-output session.
- `HOLO_HISTORY_IMAGES=2` battery A/B — the model explicitly flagged the amnesia;
  the old depth shakedown (5/17, 7/16, 7/15) predates the physical target.
- psr.exe zip from the calc run: what was Windows doing during the dead window?
- Power backend + automatic `verify_hid` post-boot with self-recovery.
- Superseded adoption: structured-output rearchitecture + resolution sync.

## Settle-threshold revalidation

Measured on the laptop's idle desktop (Notepad open, no actions): 20 consecutive
tile-max diffs at 0.25s poll — 0.0 typical, max non-spike 1.13, one 111.0 spike
(taskbar widget content churn — the I8 weather-widget class, i.e. real content,
not noise). Noise floor ≪ thresh=3.0, so idle reads stable; a widget update
mid-settle only makes settle wait longer (safe direction). **thresh=3.0
validated for the laptop panel; no change.** `drop_bottom_row` still exists in
`_frame_diff_score` for the taskbar-churn class but has no live caller after the
vm.py retirement — noted for the power-backend follow-up.

## Learned

- The `superseded` branch (07-19 evening line: structured-output rearchitecture,
  retry removal, resolution auto-sync, wire logging, var/ layout) is shelved but
  contains wanted work — its bridge is what the Pi actually ran. Adopting the
  rest (esp. resolution sync + the structured-output rearchitecture) is queued
  for a future session. Lesson: deployed-hardware state can diverge from the
  "authoritative" branch; check the appliance before assuming the repo is the
  whole truth.
- First-boot surprises were zero: the physical laptop needed NO pipeline
  changes — capture, HID, grounding, and the loop all worked first try at
  native 1080p. The VM-era failure classes (focus transfer, SPICE collapse,
  snapshot contamination, USB-passthrough dead-mouse) are simply absent.
