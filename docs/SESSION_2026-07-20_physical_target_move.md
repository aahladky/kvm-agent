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
5. `_execute` verify frames paired to the action (`wait_newer` floor).
6. Tile-max settle metric in `wait_until_stable` (flaw #4 metric fully retired).
7. `kvm_agent/hardware/target.py` — manual power/reset seam (v1).
8. `tools/battery.py` — human-graded task battery runner.
9. Config/packaging cleanup — retired-stack fields dropped, deps slimmed.
10. Docs close-out (this file, `PROJECT_STATE.md`, header/comment truth pass).

## Live shakedown results

TBD — filled in Task 11 (first run of `tools/battery.py` with
`tools/battery_tasks_shakedown.json` against the physical laptop).

## Settle-threshold revalidation

TBD — filled in Task 11 (threshold 3.0 was calibrated on the VM-era capture chain;
re-validate against the laptop panel's noise floor).

## Learned

TBD — filled in Task 11.
